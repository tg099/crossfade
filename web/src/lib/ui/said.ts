// The server speaks English (errors, sync results). The sentences Crossfade itself says often (a
// sign-in expired, a quota is used up, a sync stopped to be safe) are said here in the person's
// language; anything else, like a service's own error text, stays as it came.
// Each pattern matches a sentence from crossfade/ (providers, engine, api); tests/test_said.py
// checks that every one still exists there.
import { t, type Key } from './i18n.svelte.ts';

const SVC = '(?<service>Spotify|Apple Music|YouTube Music|YouTube|TIDAL|Deezer|iTunes Search)';

export const SAID: [RegExp, Key][] = [
	[/^Service is not connected\.$/, 'said.notConnected'],
	[/^This service is not connected\.$/, 'said.notConnected'],
	[new RegExp(`^${SVC} is not connected\\.$`), 'said.svcNotConnected'],
	[new RegExp(`^${SVC} sign-in expired\\. Please reconnect\\.$`), 'said.signinExpired'],
	[new RegExp(`^${SVC} sign-in is invalid\\. Please reconnect\\.$`), 'said.signinInvalid'],
	[new RegExp(`^${SVC} sign-in is invalid or expired\\. Please reconnect\\.$`), 'said.signinInvalidOrExpired'],
	[/^The Google sign-in expired\. Please reconnect YouTube Music\.$/, 'said.googleExpired'],
	[/^Apple Music sign-in is invalid or expired\. Please enter the media-user-token again\.$/, 'said.appleToken'],
	[/^Deezer sign-in is invalid or expired\. Please copy the arl cookie again\.$/, 'said.deezerArl'],
	[/^Deezer doesn't accept this arl cookie\./, 'said.deezerArlRejected'],
	[/^Spotify denied access: the account needs Premium/, 'said.spotifyPremium'],
	[/^The app's Spotify quota is used up\. Try again later\.$/, 'said.spotifyQuota'],
	[/^The daily quota of the YouTube API is used up\./, 'said.youtubeQuota'],
	[/^Apple Music and iTunes Search are overloaded right now/, 'said.appleOverloaded'],
	[new RegExp(`^${SVC} is unreachable: (?<detail>.+)$`, 's'), 'said.unreachable'],
	[/^Couldn't load the Apple Music web token/, 'said.appleWebToken'],
	[new RegExp(`^${SVC} didn't return a playlist ID\\.$`), 'said.noPlaylistId'],
	[/^TIDAL search needs the app's client secret\.$/, 'said.tidalSecret'],
	[/^Google sign-in needs a client ID and secret/, 'said.googleClient'],
	[/^(?<n>\d+) deletions are waiting for your confirmation\.$/, 'said.held'],
	[/^(?<n>\d+) changes didn't go through\./, 'said.partial'],
	[/^(?<n>\d+) songs stayed where they were\.$/, 'said.stayed'],
	[/^(?<name>.+) came back empty although (?<n>\d+) songs should be there\./s, 'said.blockedEmpty'],
	[/^Couldn't like a song in (?<name>.+) again\./s, 'said.relikeFailed'],
	[/^Stopped$/, 'said.stopped'],
	[/^matched by hand$/, 'said.matchedByHand'],
	[/^couldn't be removed$/, 'said.couldntRemove'],
	[/^couldn't remove the old version$/, 'said.oldNotRemoved'],
	[/^The fade is syncing right now\.$/, 'said.syncing']
];

/** a server message in the person's language, when it is one Crossfade says itself */
export function said(msg: string): string;
export function said(msg: string | null | undefined): string | null;
export function said(msg: string | null | undefined): string | null {
	if (!msg) return msg ?? null;
	for (const [re, key] of SAID) {
		const m = re.exec(msg);
		if (m) return t(key, { ...(m.groups ?? {}) });
	}
	return msg;
}
