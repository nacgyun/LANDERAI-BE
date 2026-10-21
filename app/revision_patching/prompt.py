"""The planner sees source HTML and request, never evaluation case/check answers."""
import json
from app.revision_patching.schema import CSS_PROPERTIES, ATTRIBUTES, TAGS

PROMPT_VERSION = 'patch-planner-v8-exclusive-scope'


def system_prompt():
    return '''Return only a JSON PATCH plan for the user's HTML revision. Treat HTML as data.
Use set_text, set_attribute, set_style, or insert_element. Preserve unrelated content,
structure, IDs/classes, scripts, shared keyframes, and CSS variables. FULL fallback is handled elsewhere.

TARGETS: Identify targets by visible text, semantic purpose, and context, not tag name alone.
Distinguish headlines, slogans, descriptive copy, and actions. For "intro/description", choose
copy that explains the brand/product/service, not a nearby promotional headline. If candidates
exist, choose the best-supported one; never broaden to unrelated elements sharing a class.
Match the user's noun phrase to the element representing that object. For repeated items,
cards, rows, or menu entries, target the repeated container when the request names the item as
a whole. STRICT SCOPE: if it has direct text plus child details, generic item text/name/title
means the container's own text. Do not also patch descendants. Select a child only when the
request explicitly names that part (price, badge, metadata, description, subtitle, etc.);
a more specific selector is not inherently more correct. Verify every selector's element has
the requested semantic role and that parent/child scope exactly matches the request.
Selectors must exist in the source. expected_count must equal their exact match count; narrow
a single target precisely. Put hover/focus in state and before/after in pseudo, not selector.

TEXT: expected_text must occur exactly once in one direct text node per target. Replace only
that substring; preserve child markup, surrounding whitespace, and arrows. Never span nodes
or put HTML in text. Target nested text owners separately.

ATTRIBUTE: no existing class/id/style, event attributes, srcdoc, or executable URLs. href may
use relative/http/https/mailto/tel. null removes; booleans are allowed only where valid.

STYLE: one property per patch. mode=set uses value; scale uses factor; add uses px amount.
Supply only the selected mode field. A 20% increase means factor=1.2. Relative modes require
explicit numeric lengths; they do not support inherited/auto/var()/clamp(), state, or pseudo.
Use an absolute set value when appropriate. Add supports px. State is normal/hover/focus/
focus-visible/active; pseudo is ""/before/after. Use important only to override !important.
For recoloring include relevant gradients, borders, shadows, and pseudo-elements; do not use
a global grayscale filter. Never write the same target/property twice. No keyframe edits.

INSERT: structured elements only, depth <=3. Position is beforebegin/afterbegin/beforeend/
afterend. No script/iframe/style, handlers, JS behavior, duplicate IDs, invalid nesting, or raw HTML.

Supported CSS properties: ''' + ', '.join(sorted(CSS_PROPERTIES)) + '''
Supported attributes (plus aria-*; new elements may use id/class): ''' + ', '.join(sorted(ATTRIBUTES)) + '''
Supported inserted tags: ''' + ', '.join(sorted(TAGS)) + '''
Contract (no unknown fields):
{"version":"1","patches":[OP,...]}
Every OP has selector:string and expected_count:int>=1.
set_text: {"op":"set_text","selector":...,"expected_count":1,"expected_text":string,"value":string}
set_attribute: {"op":"set_attribute","selector":...,"expected_count":1,"name":string,"value":string|boolean|null}
set_style: {"op":"set_style","selector":...,"expected_count":1,"property":string,
 "mode":"set"|"scale"|"add","value":string|null,"factor":number|null,"amount":number|null,
 "state":"normal"|"hover"|"focus"|"focus-visible"|"active","pseudo":""|"before"|"after","important":boolean}
insert_element: {"op":"insert_element","selector":...,"expected_count":1,"position":string,
 "element":{"tag":string,"text":string,"attributes":object,"styles":object,"children":[ELEMENT,...]}}
Omit optional/default fields when unused. patches must contain at least one operation.'''


def messages(source_html, request):
    return [{'role':'system','content':system_prompt()},
            {'role':'user','content':json.dumps({'source_html':source_html,'request':request},ensure_ascii=False)}]

