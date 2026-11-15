// Constants shared by the UI that don't depend on the language.
import type { Service } from './types.ts';

/** every service, in the order the UI lists them */
export const SERVICES: Service[] = ['ytm', 'apple', 'spotify', 'tidal', 'deezer'];

export const SERVICE_NAME: Record<Service, string> = {
	ytm: 'YouTube Music',
	apple: 'Apple Music',
	spotify: 'Spotify',
	tidal: 'TIDAL',
	deezer: 'Deezer'
};

/** the fade colours, soft and dusty so they sit with sage and sand; names in fade.ts COLOR_KEY.
 *  Changing them needs a migration that moves stored fades along (db.py, schema 24). */
export const LINE_COLORS = [
	{ hex: '#4A7FB8' },
	{ hex: '#CF9F3E' },
	{ hex: '#BC5E8C' },
	{ hex: '#5E9A72' },
	{ hex: '#8A9449' },
	{ hex: '#3F999B' },
	{ hex: '#8270B4' },
	{ hex: '#A3744C' }
];

/** a link to a track in its service */
export function trackUrl(service: string, id: string, url?: string | null): string {
	if (url && /^https:\/\//i.test(url)) return url;
	if (service === 'ytm') return `https://music.youtube.com/watch?v=${encodeURIComponent(id)}`;
	if (service === 'spotify') return `https://open.spotify.com/track/${encodeURIComponent(id)}`;
	if (service === 'tidal') return `https://tidal.com/browse/track/${encodeURIComponent(id)}`;
	if (service === 'deezer') return `https://www.deezer.com/track/${encodeURIComponent(id)}`;
	return `https://music.apple.com/song/${encodeURIComponent(id)}`;
}
