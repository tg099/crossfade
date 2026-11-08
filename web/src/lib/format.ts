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

/** the fade colours; their names live in the dictionaries (fade.ts COLOR_KEY) */
export const LINE_COLORS = [
	{ hex: '#0072CE' },
	{ hex: '#E4A100' },
	{ hex: '#C4007A' },
	{ hex: '#2E9E44' },
	{ hex: '#7A8B1E' },
	{ hex: '#009CA6' },
	{ hex: '#6A3FA0' },
	{ hex: '#8C5A2B' }
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
