// Colours of a cover, for backgrounds and tints (like Apple Music does with artwork).
// Real covers: sample a downscaled copy in a canvas. Missing covers: derive the same colours
// the generated stand-in gradient uses, so both paths look alike.

export function hash(s: string) {
	let h = 2166136261;
	for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
	return h >>> 0;
}

/** parameters of the generated stand-in cover (Cover.svelte without an image) */
export function seedArt(seed: string) {
	const h = hash(seed);
	const a = h % 360;
	const b = (a + 30 + ((h >> 9) % 70)) % 360;
	return { a, b, c: (a + 300) % 360, cx: 20 + ((h >> 4) % 60), cy: 15 + ((h >> 12) % 55) };
}

/** three colours: highlight, body, shadow */
export function seedColors(seed: string): string[] {
	const { a, b, c } = seedArt(seed);
	return [`hsl(${b} 80% 66%)`, `hsl(${a} 62% 50%)`, `hsl(${c} 58% 26%)`];
}

const cache = new Map<string, Promise<string[]>>();

/** dominant colours of a real image; resolves [] when the image can't be read (no CORS) */
export function imageColors(url: string): Promise<string[]> {
	const hit = cache.get(url);
	if (hit) return hit;
	const job = new Promise<string[]>((resolve) => {
		const img = new Image();
		img.crossOrigin = 'anonymous';
		img.decoding = 'async';
		img.onerror = () => resolve([]);
		img.onload = () => {
			try {
				const n = 24;
				const cv = document.createElement('canvas');
				cv.width = cv.height = n;
				const ctx = cv.getContext('2d', { willReadFrequently: true })!;
				ctx.drawImage(img, 0, 0, n, n);
				const px = ctx.getImageData(0, 0, n, n).data;
				// bucket by coarse colour, weight by saturation so grey borders don't win
				const buckets = new Map<number, { r: number; g: number; b: number; w: number }>();
				for (let i = 0; i < px.length; i += 4) {
					const [r, g, b] = [px[i], px[i + 1], px[i + 2]];
					const max = Math.max(r, g, b);
					const min = Math.min(r, g, b);
					const sat = max ? (max - min) / max : 0;
					const w = 0.25 + sat;
					const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
					const e = buckets.get(key) ?? { r: 0, g: 0, b: 0, w: 0 };
					e.r += r * w;
					e.g += g * w;
					e.b += b * w;
					e.w += w;
					buckets.set(key, e);
				}
				const top = [...buckets.values()]
					.sort((x, y) => y.w - x.w)
					.slice(0, 3)
					.map((e) => `rgb(${Math.round(e.r / e.w)} ${Math.round(e.g / e.w)} ${Math.round(e.b / e.w)})`);
				resolve(top);
			} catch {
				resolve([]); // tainted canvas
			}
		};
		img.src = url;
	});
	cache.set(url, job);
	return job;
}

/** colours for a track: its real artwork when there is one, else the stand-in */
export async function trackColors(t: { id: string; artwork?: string | null }): Promise<string[]> {
	if (t.artwork) {
		const c = await imageColors(t.artwork);
		if (c.length) return c;
	}
	return seedColors(t.id);
}

/** a stable hue per user; avatars use it as style:--uh with class "uav" (colours per theme in app.css) */
export function userHue(id: string | number): number {
	return hash(String(id)) % 360;
}
