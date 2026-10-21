/* Trusted browser adapter, called only by the Python engine. Page scripts stay disabled. */
({plan, properties, attributes, booleans}) => {
  const fail = (code, message) => { throw new Error(code + ': ' + message); };
  const logs = [], verifications = [], scopedRules = [], dirtySheets = new Set();
  const particleContexts = [];
  const allowedProperties = new Set(properties), allowedAttributes = new Set(attributes);
  const booleanAttributes = new Set(booleans);
  const forbidden = 'script,style,link,meta,base,iframe,object,embed,template';
  //목표 PATCH 태그 찾기
  const path = el => {
    const parts = [];
    while (el && el.nodeType === 1) {
      if (el.id && document.querySelectorAll('#'+CSS.escape(el.id)).length === 1) {
        parts.unshift('#'+CSS.escape(el.id)); break;
      }
      const siblings = el.parentElement ? [...el.parentElement.children] : [el];
      parts.unshift(el.localName + ':nth-child(' + (siblings.indexOf(el) + 1) + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  //h1, h2, .title {} 이런 경우 CSS 선택자 분리 -> 수정 대상에 적용되는 css 선택자만 추려내기
  const splitSelectors = s => {
    const parts = []; let start = 0, depth = 0, quote = '';
    for (let i = 0; i < s.length; i++) {
      if (s[i] === '\\') { i++; continue; }
      if (quote) { if (s[i] === quote) quote = ''; continue; }
      if (s[i] === '"' || s[i] === "'") { quote = s[i]; continue; }
      if ('(['.includes(s[i])) depth++;
      if (')]'.includes(s[i])) depth--;
      if (s[i] === ',' && depth === 0) { parts.push(s.slice(start, i)); start = i + 1; }
    }
    parts.push(s.slice(start)); return parts.map(x => x.trim());
  };
  //URL 검사 -> 프로토콜 검사
  function validateURL(value, link) {
    const compact = value.replace(/[\u0000-\u0020\u007f]/g, '');
    if (/^[a-z][a-z0-9+.-]*:/i.test(compact) &&
        !(link ? /^(https?:|mailto:|tel:)/i : /^https?:/i).test(compact))
      fail('unsafe_url', 'Unsupported URL scheme');
  }
  //HTML 속성을 실제로 수정하는 부분
  function attribute(el, name, value, inserting = false) {
    if (!(allowedAttributes.has(name) || /^aria-[a-z-]+$/.test(name) ||
          (inserting && ['id', 'class'].includes(name)))) fail('unsupported_attribute', name);
    const perTag = {href:['a'], target:['a'], rel:['a'], download:['a'],
      src:['img'], srcset:['img'], sizes:['img'], alt:['img'], loading:['img'], width:['img'], height:['img'],
      name:['input','textarea','select','button'], type:['input','button'],
      value:['input','button','option'], required:['input','textarea','select'],
      min:['input'], max:['input'], step:['input'], maxlength:['input','textarea'], minlength:['input','textarea'],
      placeholder:['input','textarea'], checked:['input'], selected:['option'],
      readonly:['input','textarea'], multiple:['input','select'],
      disabled:['button','input','select','textarea','option','optgroup','fieldset']};
    if (perTag[name] && !perTag[name].includes(el.localName)) fail('invalid_attribute_target', name);
    if (name === 'id' && (typeof value !== 'string' || !value || /\s/.test(value) || document.getElementById(value)))
      fail('duplicate_or_invalid_id', String(value));
    if (name === 'href' || name === 'src') { if (value !== null) validateURL(String(value), name === 'href'); }
    if (name === 'srcset' && value !== null) {
      for (const candidate of String(value).split(',')) {
        const parts=candidate.trim().split(/\s+/);
        if (!parts[0] || parts.length>2 || (parts[1] && !/^\d+(?:\.\d+)?[wx]$/.test(parts[1])))
          fail('invalid_srcset','Use URL plus optional width/density descriptor');
        validateURL(parts[0],false);
      }
    }
    if (typeof value === 'boolean' && !booleanAttributes.has(name)) fail('invalid_boolean_attribute', name);
    if (value === null || value === false) el.removeAttribute(name);
    else el.setAttribute(name, value === true ? '' : value);
  }
  //속성의 값이 제대로 된 값인가 확인. 이 분 때문에 여러 속성을 한꺼번에 수정 x. 1속성 수정 = 1PATCH
  function validateCSS(prop, value) {
    if (!allowedProperties.has(prop)) fail('unsupported_css_property', prop);
    // Do not let a value escape its declaration or hide URL/function tokens with escapes/comments.
    if (/[{};<>\\]/.test(value) || /\/\*|!\s*important|expression\s*\(|@/i.test(value))
      fail('invalid_css_value', prop);
    const urls = [...value.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)];
    if (/url\s*\(/i.test(value) && (prop !== 'background-image' || !urls.length))
      fail('unsafe_css_url', prop);
    for (const u of urls) validateURL(u[2], false);
    if (!CSS.supports(prop, value)) fail('invalid_css_value', prop + '=' + value);
  }
  //대상 태그의 실제 속성을 가져옴
  const computed = (el, prop, pseudo = '') => getComputedStyle(el, pseudo ? '::' + pseudo : null).getPropertyValue(prop).trim();
  //속성의 축약된 값을 확장해줌 ex)margin: 10px -> 10px 10px 10px 10px 
  function expanded(prop, value) {
    const d = document.createElement('div').style; d.setProperty(prop, value);
    const map = {padding:['top','right','bottom','left'].map(s=>'padding-'+s),
      margin:['top','right','bottom','left'].map(s=>'margin-'+s),
      'border-radius':['top-left','top-right','bottom-right','bottom-left'].map(s=>'border-'+s+'-radius')};
    if (/^border-(width|style|color)$/.test(prop)) map[prop] = ['top','right','bottom','left'].map(s=>'border-'+s+'-'+prop.slice(7));
    return (map[prop] || [prop]).map(p => [p, d.getPropertyValue(p)]);
  }
  //style sheet의 위치 찾기. 외부로 가져온 style sheet 거르기
  function sheetOwner(sheet) {
    if (!sheet.ownerNode || sheet.ownerNode.localName !== 'style') fail('external_css_unsupported', 'Inline style blocks required');
    return sheet.ownerNode;
  }
  //@media, keyframe등의 속성까지 확인하기 -> 다른 화면용 css 규칙까지 찾기 위함
  function allRules(sheet, container = sheet, out = []) {
    for (const rule of [...container.cssRules]) {
      if (rule.type === CSSRule.STYLE_RULE) out.push({sheet, container, rule});
      else if (rule.type === CSSRule.MEDIA_RULE || rule.type === CSSRule.SUPPORTS_RULE) allRules(sheet, rule, out);
      else if (rule.type === CSSRule.KEYFRAMES_RULE) continue;
      else if ('cssRules' in rule) fail('unsupported_css_group', rule.constructor.name);
      else if (rule.type === CSSRule.IMPORT_RULE) fail('external_css_unsupported', '@import');
    }
    return out;
  }
  let generatedSheet;
  //실제 속성 추가 -> CSSOM으로
  function appendRule(el, suffix, prop, value, priority) {
    //style sheet를 하나 만들어서 넣음
    if (!generatedSheet) {
      const style = document.createElement('style');
      style.setAttribute('data-landerai-patch', '1'); document.head.append(style);
      generatedSheet = style.sheet;
    }
    const index = generatedSheet.cssRules.length;
    generatedSheet.insertRule(path(el) + suffix + ' {}', index);
    const rule = generatedSheet.cssRules[index]; rule.style.setProperty(prop, value, priority);
    scopedRules.push({el, rule, suffix}); dirtySheets.add(generatedSheet);
    return rule;
  }
  //배수 적용된 값 반환
  function scaleDeclaration(raw, op) {
    const match = /^(-?(?:\d+\.?\d*|\.\d+))(px|rem|em|vw|vh|vmin|vmax|%)$/.exec(raw.trim());
    if (!match && raw.trim() !== '0') fail('unsupported_relative_value', raw);
    const number = match ? Number(match[1]) : 0, unit = match ? match[2] : 'px';
    if (op.mode === 'add' && unit !== 'px') fail('unsupported_relative_unit', 'add requires px');
    return String(op.mode === 'scale' ? number * op.factor : number + op.amount) + unit;
  }
  //기존 css 속성 값을 상대적으로 변경할 때 사용.
  function applyRelative(el, prop, op) {
    const before = computed(el, prop);
    if (!/^-?[\d.]+px$/.test(before)) fail('unsupported_computed_value', prop + '=' + before);
    const target = op.mode === 'scale' ? parseFloat(before) * op.factor : parseFloat(before) + op.amount;
    let found = false;
    // Clone each matching declaration next to its original, retaining specificity and media ancestry.
    for (const sheet of [...document.styleSheets]) {
      sheetOwner(sheet);
      for (const item of allRules(sheet)) {
        const raw = item.rule.style.getPropertyValue(prop);
        if (!raw) continue;
        const selectors = splitSelectors(item.rule.selectorText).filter(s => {
          if (/::|:(before|after)\b/.test(s)) return false;
          return el.matches(s);
        });
        if (!selectors.length) continue;
        if (/:/.test(selectors.join(' ')) && /:(hover|active|focus|focus-visible|focus-within)\b/.test(selectors.join(' ')))
          fail('relative_state_unsupported', prop);
        const value = scaleDeclaration(raw, op); validateCSS(prop, value);
        const base = selectors;
        const selector = base.map(s => s + ':where(' + path(el) + ')').join(',');
        const index = [...item.container.cssRules].indexOf(item.rule) + 1;
        item.container.insertRule(selector + ' {}', index);
        const rule = item.container.cssRules[index];
        rule.style.setProperty(prop, value, item.rule.style.getPropertyPriority(prop));
        scopedRules.push({el, rule, base}); dirtySheets.add(sheet); found = true;
      }
    }
    if (el.style.getPropertyValue(prop)) {
      const value = scaleDeclaration(el.style.getPropertyValue(prop), op);
      validateCSS(prop, value);
      el.style.setProperty(prop, value, el.style.getPropertyPriority(prop)); found = true;
    }
    if (!found) fail('relative_source_not_found', prop + ': inherited/implicit value; use explicit set');
    return {el, prop, before, target, relative:true, state:'normal', pseudo:''};
  }
  //새로운 태그나 요소 집어넣기위한 태그나 요소 만들기
  function makeElement(spec, depth = 1) {
    if (depth > 3) fail('insert_depth_exceeded', 'Maximum depth is 3');
    const el = document.createElement(spec.tag);
    for (const [k,v] of Object.entries(spec.attributes)) attribute(el,k,v,true);
    if (spec.text) el.append(document.createTextNode(spec.text));
    for (const [p,v] of Object.entries(spec.styles)) { validateCSS(p,v); el.style.setProperty(p,v); }
    for (const child of spec.children) el.append(makeElement(child,depth+1));
    return el;
  }
  //makeElement로 만든 요소에 문제가 있나 없나 확인. 중첩이 제대로 구현되어있나? 확인.
  //현재는 a <-> button, ul,ol관계, li 부모 검사, p 태그 내부 중첩 확인
  function validateStructure(el) {
    if (el.matches('a') && el.querySelector('a,button') || el.matches('button') && el.querySelector('a,button') ||
        el.matches('p') && el.querySelector('div,p,section,article,aside,h1,h2,h3,h4,h5,h6,ul,ol'))
      fail('invalid_html_structure', el.localName);
    if (el.matches('ul,ol') && [...el.children].some(x=>x.localName !== 'li')) fail('invalid_html_structure','list');
    if (el.matches('li') && !el.parentElement?.matches('ul,ol')) fail('invalid_html_structure','li parent');
  }
  // Locate adjacent text only. All Korean-language decisions are made in Python.
  function adjacentText(node, direction) {
    let current = node;
    while (current) {
      const sibling = direction === 'next' ? current.nextSibling : current.previousSibling;
      if (sibling) {
        if (sibling.nodeType === Node.TEXT_NODE) return sibling;
        // Read an inline predecessor's trailing text to avoid mistaking part of a word for a whole word.
        if (direction === 'previous') {
          let tail=sibling;
          while (tail && tail.nodeType===1 && ['inline','contents'].includes(getComputedStyle(tail).display)
                 && !tail.matches('br,script,style,textarea,select')) tail=tail.lastChild;
          if (tail?.nodeType===Node.TEXT_NODE) return tail;
        }
        return null;
      }
      const parent = current.parentElement;
      if (!parent || !['inline', 'contents'].includes(getComputedStyle(parent).display) ||
          parent.matches('script,style,textarea,select')) return null;
      current = parent;
    }
    return null;
  }
  try {
    for (const [opIndex, op] of plan.patches.entries()) {
      let targets;
      try { targets = [...document.querySelectorAll(op.selector)]; }
      catch { fail('invalid_selector', op.selector); }
      if (targets.length !== op.expected_count) fail('target_count_mismatch', op.selector + ' found=' + targets.length + ' expected=' + op.expected_count);
      if (targets.some(el => el.matches(forbidden) || el.closest('script,style,template'))) fail('unsupported_target',op.selector);
      const record = {index:opIndex, op:op.op, selector:op.selector, matched_count:targets.length, changes:[]};
      logs.push(record);
      for (const el of targets) {
        if (op.op === 'set_text') {
          const nodes = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE && n.data.includes(op.expected_text));
          if (nodes.length !== 1 || nodes[0].data.split(op.expected_text).length !== 2)
            fail('ambiguous_text', 'Expected text must occur once in a direct text node: ' + op.selector);
          const node=nodes[0], before=node.data;
          const offset=before.indexOf(op.expected_text), end=offset+op.expected_text.length;
          const suffixNode=end < before.length ? node : adjacentText(node,'next');
          const prefix=before.slice(0,offset);
          const previous=prefix ? null : adjacentText(node,'previous');
          const suffixBefore=suffixNode ? (suffixNode===node ? before.slice(end) : suffixNode.data) : '';
          // Track changed ranges so independent edits in one node remain valid.
          for (const v of verifications.filter(v=>v.kind==='text' && v.node===node)) {
            if (offset < v.end && end > v.start) fail('conflicting_patches','text overwritten');
            if (end <= v.start) {
              const delta=op.value.length-op.expected_text.length;
              v.start+=delta; v.end+=delta;
            }
          }
          for (const c of particleContexts) {
            if (c.suffixNode===node && end<=c.suffixPrefix.length) {
              c.suffixPrefix=before.slice(0,offset)+op.value+before.slice(end,c.suffixPrefix.length);
            }
          }
          node.data=node.data.replace(op.expected_text,op.value);
          particleContexts.push({opIndex,node,suffixNode,prefix,
            preceding:prefix || (previous ? previous.data : ''),
            oldWord:op.expected_text,newWord:op.value,suffixBefore,
            suffixPrefix:suffixNode===node ? prefix+op.value : ''});
          verifications.push({kind:'text',node,start:offset,end:offset+op.value.length,expected:op.value});
          record.changes.push({before,after:node.data});
        } else if (op.op === 'set_attribute') {
          const before=el.getAttribute(op.name); attribute(el,op.name,op.value);
          const after=el.getAttribute(op.name);
          verifications.push({kind:'attribute',el,name:op.name,expected:after});
          record.changes.push({name:op.name,before,after});
        } else if (op.op === 'insert_element') {
          const child=makeElement(op.element);
          if (['afterbegin','beforeend'].includes(op.position) && el.matches('img,br,hr,input,textarea,select,option')) fail('invalid_insert_parent',el.localName);
          if (['html','head','body'].includes(el.localName) && ['beforebegin','afterend'].includes(op.position)) fail('invalid_insert_position',op.position);
          el.insertAdjacentElement(op.position,child);
          if (child.parentElement?.matches('table,thead,tbody,tfoot,tr,colgroup,select,option,textarea'))
            fail('invalid_insert_parent',child.parentElement.localName);
          for (const n of [child,...child.querySelectorAll('*')]) validateStructure(n);
          for (let p=child.parentElement; p; p=p.parentElement) validateStructure(p);
          const ids=[...document.querySelectorAll('[id]')].map(x=>x.id);
          if (new Set(ids).size !== ids.length) fail('duplicate_id','Inserted ID is not unique');
          verifications.push({kind:'insert',el:child}); record.changes.push({tag:child.localName,position:op.position});
        } else {
          if (op.mode !== 'set') {
            const props = expanded(op.property,'0px').map(x=>x[0]);
            for (const prop of props) {
              const check=applyRelative(el,prop,op); verifications.push({kind:'style',...check});
              record.changes.push({property:prop,before:check.before,expected_px:check.target});
            }
          } else {
            validateCSS(op.property,op.value);
            const suffix=(op.state==='normal'?'':':'+op.state)+(op.pseudo?'::'+op.pseudo:'');
            const before=computed(el,op.property,op.pseudo);
            // Inline declarations retain their location; stylesheet declarations get target-only overrides.
            if (!suffix && el.style.getPropertyValue(op.property)) el.style.setProperty(op.property,op.value,op.important?'important':el.style.getPropertyPriority(op.property));
            else appendRule(el,suffix,op.property,op.value,op.important?'important':'');
            for (const [prop,value] of expanded(op.property,op.value))
              verifications.push({kind:'style',el,prop,value,state:op.state,pseudo:op.pseudo,relative:false});
            record.changes.push({property:op.property,before,value:op.value,state:op.state,pseudo:op.pseudo});
          }
        }
      }
    }
    // Insertions may shift nth-child positions. Rebind every generated selector to its actual node.
    for (const s of scopedRules) s.rule.selectorText=s.base
      ? s.base.map(x=>x+':where('+path(s.el)+')').join(',') : path(s.el)+s.suffix;
    for (const sheet of dirtySheets) sheetOwner(sheet).textContent=[...sheet.cssRules].map(r=>r.cssText).join('\n');
    const checks=[];
    for (const v of verifications) {
      if (v.kind==='text' && v.node.data.slice(v.start,v.end)!==v.expected) fail('conflicting_patches','text overwritten');
      if (v.kind==='attribute' && v.el.getAttribute(v.name)!==v.expected) fail('conflicting_patches','attribute overwritten');
      if (v.kind==='insert' && !v.el.isConnected) fail('insert_verification_failed','node detached');
      if (v.kind==='style') checks.push({selector:path(v.el),property:v.prop,state:v.state,pseudo:v.pseudo,
        relative:v.relative,expected_px:v.target,value:v.value});
    }
    const particle_candidates=particleContexts.map(c => {
      const owner=c.suffixNode?.parentElement;
      return {operation_index:c.opIndex,old_word:c.oldWord,new_word:c.newWord,
        preceding:c.preceding,suffix_before:c.suffixBefore,
        suffix_after:c.suffixNode ? c.suffixNode.data.slice(c.suffixPrefix.length) : '',
        selector:owner ? path(owner) : null,
        text_node_index:owner ? [...owner.childNodes].indexOf(c.suffixNode) : -1,
        prefix:c.suffixPrefix,expected_node_text:c.suffixNode?.data || '',
        connected:!!(c.node.isConnected && c.suffixNode?.isConnected &&
          (c.suffixNode===c.node || adjacentText(c.node,'next')===c.suffixNode))};
    });
    return {ok:true,logs,checks,particle_candidates};
  } catch(e) {
    const message=String(e.message || e), split=message.indexOf(':');
    return {ok:false,code:split<0?'patch_application_failed':message.slice(0,split),message,logs};
  }
}
