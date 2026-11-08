// Client-side song search for the catalog: forgiving normalisation, any word order,
// "phrases", -exclusions, relevance scoring, typo fallback and match highlighting.

const SPECIAL: Record<string, string> = {
	ß: 'ss', æ: 'ae', œ: 'oe', ø: 'o', ł: 'l', đ: 'd', ð: 'd', þ: 'th', ı: 'i',
	'&': ' ', '+': ' '
};
const MARKS = /[̀-ͯ]/g;
const APOS = /['’‘`´]/;
const ALNUM = /[\p{L}\p{N}]/u;

/** fold one character to its searchable form ('' = drop, ' ' = separator) */
function foldChar(ch: string): string {
	const lower = ch.toLocaleLowerCase('de');
	if (SPECIAL[lower] !== undefined) return SPECIAL[lower];
	if (APOS.test(lower)) return ''; // don't -> dont
	const base = lower.normalize('NFD').replace(MARKS, '');
	let out = '';
	for (const c of base) out += SPECIAL[c] ?? (ALNUM.test(c) ? c : ' ');
	return out;
}

/** folded text plus, for every folded char, the index of the original char it came from */
export function foldMap(s: string): { text: string; map: number[] } {
	let text = '';
	const map: number[] = [];
	let i = 0;
	for (const ch of s) {
		for (const c of foldChar(ch)) {
			if (c === ' ' && (text === '' || text.endsWith(' '))) continue; // collapse separators
			text += c;
			map.push(i);
		}
		i += ch.length;
	}
	if (text.endsWith(' ')) {
		text = text.slice(0, -1);
		map.pop();
	}
	return { text, map };
}

export const fold = (s: string) => foldMap(s).text;

// ------------------------------------------------------------------ query

export interface Term {
	/** folded, words separated by single spaces */
	text: string;
	/** without spaces – lets "acdc" find "AC/DC" and "ac dc" find "ACDC" */
	compact: string;
	phrase: boolean;
}
export interface Query {
	include: Term[];
	exclude: Term[];
	/** whole query folded, for "title equals query" bonuses */
	full: string;
}

export function parseQuery(raw: string): Query {
	const include: Term[] = [];
	const exclude: Term[] = [];
	const re = /(-?)(?:"([^"]*)"?|(\S+))/g;
	for (const m of raw.matchAll(re)) {
		const neg = m[1] === '-' && (m[2] !== undefined || (m[3] ?? '').length > 0);
		const phrase = m[2] !== undefined;
		const text = fold(phrase ? m[2] : m[3]);
		if (!text) continue;
		const t = { text, compact: text.replaceAll(' ', ''), phrase };
		(neg ? exclude : include).push(t);
	}
	return { include, exclude, full: include.map((t) => t.text).join(' ') };
}

// ------------------------------------------------------------------ index

export interface Field {
	text: string;
	compact: string;
	words: string[];
}
export interface Entry {
	title: Field;
	artist: Field;
	album: Field;
}
const field = (s: string): Field => {
	const text = fold(s);
	return { text, compact: text.replaceAll(' ', ''), words: text ? text.split(' ') : [] };
};
export const indexEntry = (title: string, artist: string, album: string): Entry => ({
	title: field(title),
	artist: field(artist),
	album: field(album)
});

// ------------------------------------------------------------------ scoring

const WEIGHT = { title: 1, artist: 0.85, album: 0.5 } as const;

/** best literal score of a term in one field (0 = no match) */
function literal(t: Term, f: Field): number {
	if (!f.text) return 0;
	if (f.text === t.text) return 100;
	if (f.text.startsWith(t.text)) return 70;
	const at = f.text.indexOf(t.text);
	if (at > 0 && f.text[at - 1] === ' ') return 50; // starts a word
	if (at > 0) return t.phrase ? 0 : 20;
	if (!t.phrase && t.compact.length >= 3 && f.compact.includes(t.compact)) return 15;
	return 0;
}

/** Damerau–Levenshtein limited to `max`; returns max+1 when exceeded */
function within(a: string, b: string, max: number): boolean {
	if (Math.abs(a.length - b.length) > max) return false;
	const n = b.length;
	let pp = new Array<number>(n + 1).fill(0);
	let p = Array.from({ length: n + 1 }, (_, j) => j);
	for (let i = 1; i <= a.length; i++) {
		const c = [i];
		let best = i;
		for (let j = 1; j <= n; j++) {
			const cost = a[i - 1] === b[j - 1] ? 0 : 1;
			let v = Math.min(p[j] + 1, c[j - 1] + 1, p[j - 1] + cost);
			if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, pp[j - 2] + 1);
			c.push(v);
			if (v < best) best = v;
		}
		if (best > max) return false;
		pp = p;
		p = c;
	}
	return p[n] <= max;
}

/** typo-tolerant word match: "beetles" ~ "beatles", "metalic" ~ "metallica" (as a prefix) */
function fuzzy(t: Term, f: Field): number {
	if (t.phrase || t.text.includes(' ') || t.text.length < 4) return 0;
	const max = t.text.length >= 8 ? 2 : 1;
	for (const w of f.words) {
		if (w.length < 3) continue;
		if (within(t.text, w, max)) return 10;
		if (w.length > t.text.length && within(t.text, w.slice(0, t.text.length), max)) return 6;
	}
	return 0;
}

function termScore(t: Term, e: Entry, allowFuzzy: boolean): number {
	let best = 0;
	for (const k of ['title', 'artist', 'album'] as const) best = Math.max(best, literal(t, e[k]) * WEIGHT[k]);
	if (best || !allowFuzzy) return best;
	for (const k of ['title', 'artist', 'album'] as const) best = Math.max(best, fuzzy(t, e[k]) * WEIGHT[k]);
	return best;
}

/** relevance of a song for the query; 0 = not a hit */
export function score(q: Query, e: Entry, allowFuzzy = false): number {
	for (const t of q.exclude) if (termScore(t, e, false)) return 0;
	if (!q.include.length) return 1;
	let sum = 0;
	for (const t of q.include) {
		const s = termScore(t, e, allowFuzzy);
		if (!s) return 0;
		sum += s;
	}
	// the whole query names the title / artist / "artist title" outright
	if (q.include.length > 1) {
		if (e.title.text === q.full) sum += 80;
		else if (e.title.text.startsWith(q.full)) sum += 40;
		if (e.artist.text === q.full) sum += 40;
		const combo = `${e.artist.text} ${e.title.text}`;
		const combo2 = `${e.title.text} ${e.artist.text}`;
		if (combo.startsWith(q.full) || combo2.startsWith(q.full)) sum += 30;
	}
	return sum;
}

export interface SearchResult {
	scores: Map<number, number>;
	/** true when nothing matched literally and the typo-tolerant pass was used */
	fuzzy: boolean;
}

export function search<T extends { id: number }>(items: T[], index: Map<number, Entry>, raw: string): SearchResult | null {
	const q = parseQuery(raw);
	if (!q.include.length && !q.exclude.length) return null;
	const run = (allowFuzzy: boolean) => {
		const scores = new Map<number, number>();
		for (const it of items) {
			const e = index.get(it.id);
			const s = e ? score(q, e, allowFuzzy) : 0;
			if (s) scores.set(it.id, s);
		}
		return scores;
	};
	const exact = run(false);
	if (exact.size || !q.include.length) return { scores: exact, fuzzy: false };
	return { scores: run(true), fuzzy: true };
}

/** does a plain label contain every word of the query (any order)? – for the category lists */
export function labelMatches(label: string, raw: string): boolean {
	const q = parseQuery(raw);
	const f = field(label);
	return q.include.every((t) => literal(t, f) > 0) && !q.exclude.some((t) => literal(t, f) > 0);
}

// ------------------------------------------------------------------ highlighting

export interface Segment {
	s: string;
	hit: boolean;
}

/** split `text` into plain and highlighted parts for the query's literal hits */
export function highlight(text: string, raw: string): Segment[] {
	const terms = parseQuery(raw).include;
	if (!terms.length || !text) return [{ s: text, hit: false }];
	const { text: f, map } = foldMap(text);
	const ranges: [number, number][] = [];
	for (const t of terms) {
		for (let at = f.indexOf(t.text); at >= 0; at = f.indexOf(t.text, at + 1)) {
			const start = map[at];
			const last = at + t.text.length - 1;
			// end = start of the next original char after the last folded char
			const end = last + 1 < map.length ? map[last + 1] : text.length;
			ranges.push([start, Math.max(end, start + 1)]);
		}
	}
	if (!ranges.length) return [{ s: text, hit: false }];
	ranges.sort((a, b) => a[0] - b[0]);
	const out: Segment[] = [];
	let pos = 0;
	let [cs, ce] = ranges[0];
	const flush = () => {
		if (cs > pos) out.push({ s: text.slice(pos, cs), hit: false });
		// trim trailing separators that got pulled in (e.g. "AC/" for "ac")
		let e = ce;
		while (e > cs + 1 && !ALNUM.test(text[e - 1])) e--;
		out.push({ s: text.slice(cs, e), hit: true });
		pos = e;
	};
	for (const [s, e] of ranges.slice(1)) {
		if (s <= ce) ce = Math.max(ce, e);
		else {
			flush();
			[cs, ce] = [s, e];
		}
	}
	flush();
	if (pos < text.length) out.push({ s: text.slice(pos), hit: false });
	return out;
}
