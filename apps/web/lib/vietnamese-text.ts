/** Whole-word keys retain vowel shapes and tones, including old/new tone placement. */
function ethnonym(text: string) {
  return text.replace(/h['’]m[oô]ng/giu, (word) => word.replace(/['’]/u, ""));
}

export function surface(text: string) {
  return (ethnonym(text.normalize("NFC")).toLocaleLowerCase("vi").match(/[\p{L}\p{N}]+/gu) ?? []).join(" ");
}

export function normalize(text: string) {
  return ethnonym(text.normalize("NFC")).normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function accentKey(word: string) {
  const decomposed = word.normalize("NFD").toLocaleLowerCase("vi");
  const tones = [...decomposed].filter((char) => /[\u0300\u0301\u0303\u0309\u0323]/u.test(char)).sort().join("");
  const letters = decomposed.replace(/[\u0300\u0301\u0303\u0309\u0323]/gu, "").normalize("NFC");
  return `${letters}|${tones}`;
}

export type LookupToken = { word: string; folded: string; marked: string };
export type LookupText = { tokens: LookupToken[]; folded: Set<string>; marked: Set<string>; ascii: boolean };

export function compileText(text: string): LookupText {
  const value = surface(text);
  const tokens = value.split(" ").filter(Boolean).map((word) => ({ word, folded: normalize(word), marked: accentKey(word) }));
  return { tokens, folded: new Set(tokens.map((t) => t.folded)), marked: new Set(tokens.map((t) => t.marked)), ascii: value === normalize(value) };
}

export function hasToken(text: LookupText, query: LookupToken, strict: boolean) {
  return text.folded.has(query.folded) && (!strict || text.ascii || text.marked.has(query.marked));
}

export function compiledPhrasePosition(text: LookupText, query: LookupText, strict = !query.ascii) {
  if (!query.tokens.length) return -1;
  for (let start = 0; start <= text.tokens.length - query.tokens.length; start++) {
    if (query.tokens.every((target, offset) => {
      const word = text.tokens[start + offset];
      return word.folded === target.folded && (!strict || text.ascii || word.marked === target.marked);
    })) return start;
  }
  return -1;
}

export function tokenMatches(word: string, query: string, strict = false) {
  if (normalize(word) !== normalize(query)) return false;
  return (!strict && (word === normalize(word) || query === normalize(query))) || accentKey(word) === accentKey(query);
}

export function phrasePosition(text: string, query: string, strictQuery?: boolean) {
  return compiledPhrasePosition(compileText(text), compileText(query), strictQuery);
}


export function ambiguousForms(fields: LookupText[], query: LookupText) {
  if (!query.ascii || !query.tokens.length) return [];
  const forms = new Map<string, string>();
  for (const field of fields) {
    if (field.ascii) continue;
    const position = compiledPhrasePosition(field, query);
    if (position >= 0) {
      const tokens = field.tokens.slice(position, position + query.tokens.length);
      const phrase = tokens.map((t) => t.word).join(" ");
      const suggestion = phrase === normalize(phrase) || position > 0 ? field.tokens.map((t) => t.word).join(" ") : phrase;
      forms.set(tokens.map((t) => t.marked).join(" "), suggestion);
    }
  }
  return forms.size > 1 ? [...forms.values()].sort().slice(0, 5) : [];
}
