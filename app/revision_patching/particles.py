"""Conservative Korean particle correction scoped to PATCH text replacements."""
import re
import unicodedata


PAIRS = (("으로", "로"), ("을", "를"), ("이", "가"), ("은", "는"), ("과", "와"), ("아", "야"))
HANGUL = re.compile(r"[가-힣]+(?: +[가-힣]+)*")


def choose_particle(word: str, pair: tuple[str, str]) -> str:
    final = (ord(word[-1]) - 0xAC00) % 28
    if pair == ("으로", "로"):
        return "로" if final in (0, 8) else "으로"
    return pair[0] if final else pair[1]


def boundary(text: str) -> bool:
    return not text or text[0].isspace() or unicodedata.category(text[0]).startswith("P")


def leading_particle(text: str):
    for pair in PAIRS:
        for particle in pair:
            if text.startswith(particle) and boundary(text[len(particle):]):
                return pair, particle
    return None


def decide(candidate: dict) -> dict[str, str]:
    old, new = candidate["old_word"], candidate["new_word"]

    def skip(reason: str) -> dict[str, str]:
        return {"status": "skipped", "reason": reason}

    if not HANGUL.fullmatch(old) or not HANGUL.fullmatch(new):
        return skip("non_hangul_or_phrase")
    if old == new:
        return skip("unchanged_word")
    preceding = candidate.get("preceding", "")
    if preceding and (preceding[-1].isalnum() or preceding[-1] == "_"):
        return skip("partial_word")
    if not candidate.get("connected"):
        return skip("no_adjacent_text")
    original = leading_particle(candidate["suffix_before"])
    current = leading_particle(candidate["suffix_after"])
    if original != current:
        return skip("adjacent_particle_already_edited")
    if current is None:
        return skip("no_supported_isolated_particle")
    pair, particle = current
    wanted = choose_particle(new, pair)
    if particle == wanted:
        return skip("already_correct")
    if particle != choose_particle(old, pair):
        return skip("original_particle_inconsistent")
    return {
        "status": "corrected",
        "reason": "hangul_final_consonant_rule",
        "particle_before": particle,
        "particle_after": wanted,
    }


def correct_particles(page, candidates: list[dict], enabled: bool = True) -> list[dict]:
    records = []
    for candidate in candidates:
        decision = decide(candidate) if enabled else {"status": "skipped", "reason": "disabled"}
        record = {"source": "python_particle_correction", **candidate, **decision}
        if decision["status"] == "corrected":
            outcome = page.evaluate("""c => {
                const matches=document.querySelectorAll(c.selector);
                if(matches.length!==1)return {ok:false};
                const node=matches[0].childNodes[c.text_node_index];
                if(!node || node.nodeType!==3 || node.data!==c.expected_node_text)return {ok:false};
                const before=node.data, offset=c.prefix.length;
                if(before.slice(offset,offset+c.particle_before.length)!==c.particle_before)return {ok:false};
                node.data=before.slice(0,offset)+c.particle_after+before.slice(offset+c.particle_before.length);
                return {ok:true,before,after:node.data};
            }""", {**candidate, **decision})
            if not outcome["ok"]:
                record.update(status="skipped", reason="stale_text_location")
            else:
                record.update(node_text_before=outcome["before"], node_text_after=outcome["after"])
        records.append(record)
    return records
