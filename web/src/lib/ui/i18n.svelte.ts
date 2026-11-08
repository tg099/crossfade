// Languages: German and English. 'system' (the default) follows the browser / OS language;
// the choice is stored with the user's account (see ui.setLang). English is the fallback.
import { de } from './lang/de.ts';
import { en } from './lang/en.ts';

export type Lang = 'de' | 'en';
export type LangPref = 'system' | Lang;
export type Key = keyof typeof de;
type Vars = Record<string, string | number>;

const DICTS: Record<Lang, Record<Key, string | ((v: Vars) => string)>> = { de, en };

function detect(): Lang {
	if (typeof navigator === 'undefined') return 'en';
	for (const l of navigator.languages?.length ? navigator.languages : [navigator.language]) {
		const c = l.toLowerCase();
		if (c.startsWith('de')) return 'de';
		if (c.startsWith('en')) return 'en';
	}
	return 'en';
}

class I18n {
	pref = $state<LangPref>('system');
	system = $state<Lang>(detect());
	lang = $derived<Lang>(this.pref === 'system' ? this.system : this.pref);
	/** BCP 47 locale for dates and numbers */
	locale = $derived(this.lang === 'de' ? 'de-DE' : navigator.language?.toLowerCase().startsWith('en') ? navigator.language : 'en-GB');

	set(p: LangPref) {
		this.pref = p === 'de' || p === 'en' ? p : 'system';
		this.system = detect();
		document.documentElement.lang = this.lang;
	}
}
export const i18n = new I18n();

/** translate; entries are strings with {placeholders} or functions (plurals, grammar) */
export function t(key: Key, vars: Vars = {}): string {
	const e = DICTS[i18n.lang][key] ?? DICTS.en[key];
	if (typeof e === 'function') return e(vars);
	return e.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''));
}

export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString(i18n.locale);

/** a rough span of time: "25 Min." under an hour, "1,5 Std." / "12 Std." above */
export function duration(seconds: number): string {
	const min = Math.max(1, Math.round(seconds / 60));
	if (min < 60) return t('dur.min', { n: min });
	const h = min / 60;
	return t('dur.h', { n: (h < 10 ? Math.round(h * 2) / 2 : Math.round(h)).toLocaleString(i18n.locale) });
}
