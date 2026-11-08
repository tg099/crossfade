// The open line's colour is the app's tint. Line colours are picked for the transit-map look,
// not for text contrast, so derive text-safe variants for light and dark backgrounds.

type RGB = [number, number, number];

const hex2rgb = (hex: string): RGB => {
	const h = hex.replace('#', '');
	return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
};
const rgb2hex = (c: RGB) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
const lin = (v: number) => {
	const s = v / 255;
	return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const lum = ([r, g, b]: RGB) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
const contrast = (a: RGB, b: RGB) => {
	const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
	return (x + 0.05) / (y + 0.05);
};
const mix = (a: RGB, b: RGB, t: number): RGB => a.map((v, i) => v + (b[i] - v) * t) as RGB;

const WHITE: RGB = [255, 255, 255];
const BLACK: RGB = [0, 0, 0];
const CELL_DARK: RGB = [28, 28, 30];

function until(base: RGB, toward: RGB, bg: RGB, ratio: number): RGB {
	for (let t = 0; t <= 1; t += 0.04) {
		const c = mix(base, toward, t);
		if (contrast(c, bg) >= ratio) return c;
	}
	return toward;
}

const cache = new Map<string, string>();

/** CSS custom properties for a `.tinted` element. */
export function tintVars(hex: string): string {
	const hit = cache.get(hex);
	if (hit) return hit;
	const base = hex2rgb(hex);
	const textLight = until(base, BLACK, WHITE, 4.5);
	const textDark = until(base, WHITE, CELL_DARK, 4.5);
	const fillDark = mix(base, WHITE, 0.12);
	const on = (c: RGB) => (contrast(c, WHITE) >= 3 ? '#ffffff' : '#000000');
	const v =
		`--tint-l:${rgb2hex(textLight)};--tint-d:${rgb2hex(textDark)};` +
		`--fill-l:${hex};--fill-d:${rgb2hex(fillDark)};` +
		`--on-fill-l:${on(base)};--on-fill-d:${on(fillDark)};--raw:${hex}`;
	cache.set(hex, v);
	return v;
}

/** Ink that reads on the raw line colour (badges). */
export function onColor(hex: string) {
	return contrast(hex2rgb(hex), WHITE) >= 3 ? '#ffffff' : '#000000';
}
