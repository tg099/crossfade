export type Service = 'ytm' | 'apple' | 'spotify' | 'tidal' | 'deezer';
export type Kind = 'liked' | 'library' | 'playlist';
export type Role = 'both' | 'send' | 'receive';

export interface User {
	id: number;
	username: string;
	display_name: string;
	is_admin: boolean;
	must_change_password: boolean;
	theme: 'system' | 'light' | 'dark';
	lang: 'system' | 'de' | 'en';
	release_pref: Release;
	share_stats: boolean;
	/** the first steps on the home page were finished or put away */
	intro_done: boolean;
}

export interface AdminUser {
	id: number;
	username: string;
	display_name: string;
	is_admin: boolean;
	must_change_password: boolean;
	created_at: number;
	lines: number;
	services: Service[];
}

export interface Activity {
	items: { song_id: number; ts: number; label: string; line_id: number; line_name: string; color: string; service: Service | null; artwork: string | null; title: string | null; artist: string | null }[];
	last_24h: { added: number; removed: number };
	week: { days: number[]; total: number; prev_total: number; saved_s: number; top_artist: { name: string; songs: number } | null; top_line: number | null };
}

export interface Board {
	enabled: boolean;
	days: number;
	people: { id: number; username: string; display_name: string; songs: number; me: boolean }[];
}

export interface FadeStats {
	songs: number;
	complete: number;
	syncs: number;
	since: number;
	added: number;
	removed: number;
	failed_30d: number;
	saved_s: number;
	members: { member_id: number; added: number; removed: number }[];
}

export interface ServiceInfo {
	service: Service;
	name: string;
	connected: boolean;
	account_id: number | null;
	display_name: string | null;
	status: 'ok' | 'error' | 'disconnected';
	status_message: string | null;
	updated_at: number | null;
	methods: ('oauth' | 'device' | 'paste' | 'token')[];
	lines: number;
	admin_setup_needed: boolean;
	/** YouTube only: songs land there as music video or as audio track */
	yt_format: YtFormat | null;
	/** last time Crossfade tried the login, when it ends (if the service says) and when it was entered */
	checked_at: number | null;
	expires_at: number | null;
	connected_at: number | null;
}

export type YtFormat = 'song' | 'video';
/** which release a duplicate keeps: the album track or the single */
export type Release = 'album' | 'single';

export interface CollectionInfo {
	kind: Kind;
	id: string;
	name: string;
	count: number | null;
	editable: boolean;
}

export interface Member {
	id: number;
	line_id: number;
	account_id: number;
	/** whose collection it is: in a shared fade, a friend's */
	user_id: number;
	user_name: string;
	mine: boolean;
	service: Service;
	kind: Kind;
	collection_id: string;
	collection_name: string;
	role: Role;
	follow_deletions: boolean; // send-only: still applies deletions made elsewhere
	position: number;
	item_count: number | null;
	initialized: boolean;
	pending: number;
	unmatched: number;
	account_status: string;
	account_message: string | null;
	account_connected: boolean;
	covers: string[];
}

export interface Job {
	line_id: number;
	state: 'queued' | 'running';
	mode: 'apply' | 'preview';
	trigger: string;
	phase: string;
	done: number;
	total: number;
	started_at: number;
	/** how many live results the running job has produced so far (GET /api/lines/:id/live) */
	live_n?: number;
}

/** One result of a running job, as it happened */
export type LiveItem =
	| { kind: 'start'; mode: 'apply' | 'preview'; members: Omit<PlanMember, 'add' | 'remove' | 'pending'>[] }
	| { kind: 'plan'; group: 'add' | 'remove' | 'pending' | 'held'; member_id?: number; track: TrackInfo }
	| { kind: 'issue'; member_id: number; song_id: number; track: TrackInfo; candidates: TrackInfo[]; reason: string | null }
	| ({ kind: 'change'; song_id: number } & Omit<Change, 'id'>);

export interface Warning {
	kind: string;
	text: string;
	member_id?: number;
	line_id?: number;
}

export interface Line {
	id: number;
	number: number;
	/** your own fade; false = a friend's fade you joined with a collection */
	mine: boolean;
	owner: Person;
	/** owner only: friends invited who have not joined yet, with what they asked to join with */
	invites: { id: number; created_at: number; user: Person; request: JoinRequest | null }[];
	name: string;
	color: string;
	icon: string;
	sync_deletions: boolean;
	interval_minutes: number;
	delete_guard_min: number;
	delete_guard_ratio: number;
	paused: boolean;
	version_policy: 'closest' | 'exact';
	last_run_at: number | null;
	last_status: string | null;
	last_summary: Record<string, any> | null;
	position: number;
	created_at: number;
	members: Member[];
	songs: number;
	issues: number;
	warnings: Warning[];
	job: Job | null;
	next_run_at: number | null;
}

// ------------------------------------------------------------------ friends
/** all a friend learns about a person */
export interface Person {
	id: number;
	username: string;
	display_name: string;
}

export interface Friend extends Person {
	since: number | null;
	/** fades you share (yours with them in it, or theirs with you in it) */
	shared: number;
}

export interface FriendRequest {
	id: number;
	user: Person;
	created_at: number;
}

/** the collection and direction a friend asked to join a fade with, until the owner decides */
export interface JoinRequest {
	service: Service;
	kind: Kind;
	collection_name: string;
	role: Role;
	follow_deletions: boolean;
	/** a new playlist, created once the owner approves */
	create: boolean;
	requested_at: number;
}

/** an invitation into a friend's fade: only its name and look, until you join */
export interface FadeInvite {
	id: number;
	created_at: number;
	fade: { name: string; icon: string; color: string };
	from: Person;
	/** what you asked to join with, while the owner hasn't decided */
	request: JoinRequest | null;
}

export interface Friends {
	friends: Friend[];
	incoming: FriendRequest[];
	outgoing: FriendRequest[];
	invites: FadeInvite[];
}

export interface TrackInfo {
	service: Service | '';
	id: string;
	title: string;
	artist: string;
	album: string;
	duration: number | null;
	isrc: string | null;
	url: string | null;
	explicit?: boolean | null;
	album_type?: string | null;
	video_type?: string | null;
	artwork?: string | null;
	score?: number;
	exact?: boolean;
	notes?: string[];
	note?: string;
	version?: string;
	song_id?: number;
}

export interface Issue {
	id: number;
	kind: 'unmatched' | 'ignored';
	member_id: number;
	song_id: number;
	collection_name: string;
	service: Service;
	created_at: number;
	data: { track: TrackInfo; candidates?: TrackInfo[]; reason?: string | null };
}

export interface PlanMember {
	member_id: number;
	service: Service;
	name: string;
	role: Role;
	count: number;
	add: TrackInfo[];
	remove: TrackInfo[];
	pending: TrackInfo[];
}

export interface Plan {
	run_id: number;
	trigger: string;
	started_at: number;
	status: string;
	summary: Record<string, any>;
	plan: {
		summary: Record<string, number>;
		members: PlanMember[];
		held: TrackInfo[];
		unmatched: (TrackInfo & { member_id: number })[];
	};
}

export interface Change {
	id: number;
	run_id: number | null;
	action: 'add' | 'remove';
	label: string;
	ok: number;
	message: string | null;
	note: string | null;
	artwork: string | null;
	ts: number;
	member_id: number | null;
	collection_name: string | null;
	service: Service | null;
	song_id: number | null;
}

export interface VersionStop {
	member_id: number;
	service: Service;
	collection_name: string;
	state: 'ok' | 'pending' | 'excluded' | 'missing';
	track: TrackInfo | null;
	version: string | null;
	exact: boolean | null;
	notes: string[];
}

export interface SongDetail {
	song_id: number;
	origin: TrackInfo;
	origin_version: string;
	stops: VersionStop[];
}

export interface VersionAuditEntry {
	song_id: number;
	origin: TrackInfo;
	stops: { member_id: number; service: Service; collection_name: string; track: TrackInfo; notes: string[] }[];
}

export interface ActivityItem {
	song_id: number;
	ts: number;
	label: string;
	title: string | null;
	artist: string | null;
	artwork: string | null;
	line_id: number;
	line_name: string;
	color: string;
	service: Service | null;
}

export interface Activity {
	items: ActivityItem[];
	last_24h: { added: number; removed: number };
}

// ------------------------------------------------------------------ catalog
export interface CatalogSource {
	id: number;
	account_id: number;
	service: Service;
	kind: Kind;
	collection_id: string;
	name: string;
	editable: boolean;
	included: boolean;
	item_count: number | null;
	scanned_at: number | null;
	error: string | null;
	/** lines this collection is a stop of */
	lines: number[];
}

/** One song, compact: t title, a artist, al album, d seconds, c cover, x explicit,
 * v only music videos, f first seen (unix s), in [source id, service ref id][]. */
export interface CatalogSong {
	id: number;
	t: string;
	a: string;
	al: string;
	d: number | null;
	c: string | null;
	x: boolean;
	v: boolean;
	f: number;
	in: [number, string][];
}

export interface CatalogLine {
	id: number;
	number: number;
	name: string;
	color: string;
	songs: number[];
}

export interface CatalogJob {
	action: 'scan' | 'remove' | 'add' | 'dedupe' | 'swap' | 'playlists' | 'undo';
	state: 'queued' | 'running';
	phase: string;
	done: number;
	total: number;
	started_at: number;
}

export interface CatalogFinished {
	action: CatalogJob['action'];
	status: 'ok' | 'partial' | 'stopped' | 'auth_error' | 'error';
	message: string | null;
	result: Record<string, any>;
	at: number;
}

export interface Catalog {
	sources: CatalogSource[];
	songs: CatalogSong[];
	lines: CatalogLine[];
	scanned_at: number | null;
	job: CatalogJob | null;
}

// ------------------------------------------------------------------ tidying the library
/** one service entry of a song in a duplicate group; id is "service:ref" */
export interface CleanupVariant {
	id: string;
	service: Service;
	ref_id: string;
	song_id: number;
	title: string;
	artist: string;
	album: string;
	duration: number | null;
	version: string;
	/** null: not known (videos, compilations, no album) */
	release: Release | null;
	artwork: string | null;
	video: boolean;
	explicit: number | null;
	sources: number[];
}

export interface CleanupGroup {
	key: string;
	/** same: one song entered twice; likely: same title, artist and length; check: lengths differ */
	confidence: 'same' | 'likely' | 'check';
	songs: number[];
	title: string;
	artist: string;
	artwork: string | null;
	/** suggested variant id */
	keep: string;
	variants: CleanupVariant[];
	/** variant id -> entries [source id, ref] removed when keeping it */
	plans: Record<string, [number, string][]>;
}

export interface CleanupCopy {
	source_id: number;
	ref_id: string;
	song_id: number;
	copies: number;
	title: string;
	artist: string;
	artwork: string | null;
}

export interface CleanupUnusual {
	song_id: number;
	key: string;
	tags: string[];
	title: string;
	artist: string;
	artwork: string | null;
	sources: number[];
}

export interface CleanupPlaylist {
	key: string;
	kind: 'empty' | 'identical' | 'subset' | 'similar' | 'same_name';
	/** smaller first for subset/similar */
	sources: number[];
	common?: number;
	only?: [number, number];
}

export interface CleanupSwap {
	source_id: number;
	ref_id: string;
	song_id: number;
	title: string;
	artist: string;
	artwork: string | null;
}

export interface Cleanup {
	duplicates: CleanupGroup[];
	copies: CleanupCopy[];
	unusual: CleanupUnusual[];
	playlists: CleanupPlaylist[];
	release: Release;
	youtube: { account_id: number; format: YtFormat; candidates: CleanupSwap[]; without: number } | null;
	fades: Record<string, { id: number; name: string }[]>;
	dismissed: number;
	undo: { at: number; action: string; n: number } | null;
}
