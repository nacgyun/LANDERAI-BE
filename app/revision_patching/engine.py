"""Python-controlled atomic DOM/CSS application using Chromium's native CSS parser."""
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
from app.revision_patching.schema import PatchPlan, CSS_PROPERTIES, ATTRIBUTES, BOOLEAN_ATTRIBUTES
from app.revision_patching.particles import correct_particles

ENGINE_VERSION = 'patch-engine-v2-python-particles'
ADAPTER = (Path(__file__).parent / 'dom_engine.js').read_text(encoding='utf-8')


class PatchError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(f'{code}: {message}')
        self.code, self.details = code, details or {}


def apply_patch_plan(source_html, plan, chromium_path=None, *, correct_korean_particles=True):
    """Return HTML only after the complete plan passes. Never writes partial output.

    Scripts/network are disabled while applying. Browser DOM serialization may normalize
    formatting. Application checks are at desktop 1440x900; media rules are retained,
    but this function does not claim mobile/user-intent/preservation evaluation.
    """
    started = time.perf_counter()
    plan = plan if isinstance(plan, PatchPlan) else PatchPlan.model_validate(plan)
    with sync_playwright() as p:
        kwargs = {'headless': True}
        if chromium_path:
            kwargs['executable_path'] = chromium_path
        browser = p.chromium.launch(**kwargs)
        try:
            context = browser.new_context(java_script_enabled=False, viewport={'width':1440,'height':900},
                                          service_workers='block')
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(source_html, wait_until='domcontentloaded')
            result = page.evaluate(ADAPTER, {'plan':plan.model_dump(), 'properties':sorted(CSS_PROPERTIES),
                                            'attributes':sorted(ATTRIBUTES), 'booleans':sorted(BOOLEAN_ATTRIBUTES)})
            if not result['ok']:
                raise PatchError(result['code'], result['message'], {'operations':result['logs']})
            particle_records = correct_particles(page, result.get('particle_candidates', []), enabled=correct_korean_particles)
            serialized = page.content()
            page.set_content(serialized, wait_until='domcontentloaded')
            if page.content() != serialized:
                raise PatchError('invalid_html_structure', 'HTML changes structure when reparsed')
            # Hover/focus checks use CDP state forcing, so page event handlers never run.
            session = context.new_cdp_session(page)
            session.send('DOM.enable'); session.send('CSS.enable')
            root = session.send('DOM.getDocument')['root']['nodeId']
            verification = []
            for check in result['checks']:
                node_id = session.send('DOM.querySelector', {'nodeId':root,'selector':check['selector']})['nodeId']
                if not node_id:
                    raise PatchError('target_lost', check['selector'])
                states = [] if check['state']=='normal' else [check['state']]
                session.send('CSS.forcePseudoState', {'nodeId':node_id,'forcedPseudoClasses':states})
                # Wait out existing transitions; reject an endlessly moving target instead of claiming success.
                page.wait_for_timeout(30)
                actual = page.locator(check['selector']).evaluate('''(el,c) => {
                    for (const a of el.getAnimations()) { try { a.finish(); } catch {} }
                    return getComputedStyle(el,c.pseudo?'::'+c.pseudo:null).getPropertyValue(c.property).trim();
                }''',check)
                if check['relative']:
                    try:
                        passed = actual.endswith('px') and abs(float(actual[:-2])-check['expected_px']) <= max(.1,abs(check['expected_px'])*.002)
                    except ValueError:
                        passed = False
                    expected = check['expected_px']
                else:
                    # Resolve expected value on the same element/context with a temporary strongest declaration.
                    expected = page.locator(check['selector']).evaluate('''(el,c) => {
                        const style=document.createElement('style');
                        const suffix=c.pseudo?'::'+c.pseudo:'';
                        const selector=c.selector+':not(#landerai-verification-never-id)'.repeat(32)+suffix;
                        style.textContent=selector+' {'+c.property+':'+c.value+' !important}';
                        const old=el.getAttribute('style');
                        if (!c.pseudo) el.style.setProperty(c.property,c.value,'important');
                        document.head.append(style);
                        for (const a of el.getAnimations()) { try { a.finish(); } catch {} }
                        const value=getComputedStyle(el,c.pseudo?'::'+c.pseudo:null).getPropertyValue(c.property).trim();
                        style.remove(); if(old===null)el.removeAttribute('style');else el.setAttribute('style',old);
                        return value;
                    }''',check)
                    passed = actual == expected
                session.send('CSS.forcePseudoState', {'nodeId':node_id,'forcedPseudoClasses':[]})
                verification.append({**check,'actual':actual,'expected':expected,'passed':passed})
                if not passed:
                    raise PatchError('style_verification_failed', f"{check['selector']} {check['property']}: {actual} != {expected}",
                                     {'operations':result['logs'],'verification':verification})
            html = page.content()
            return {'html':html, 'engine_version':ENGINE_VERSION, 'operations':result['logs'],
                    'particle_corrections':particle_records,
                    'effective_plan':{'model_patches':plan.model_dump()['patches'],
                                      'python_text_edits':[r for r in particle_records if r['status']=='corrected']},
                    'verification':verification, 'viewport':{'width':1440,'height':900},
                    'application_ms':round((time.perf_counter()-started)*1000,1)}
        finally:
            browser.close()

