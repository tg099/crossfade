<script lang="ts">
	import McpPreview from './McpPreview.svelte';
	import { num, t, type Key } from './i18n.svelte.ts';
	import { mmss } from './ui.svelte.ts';

	// What an assistant proposed (or what came of it), readable: known fields get a label, ids and
	// internals are left out, songs show as "title · artist", long lists stop after a few dozen.
	let { value, depth = 0 }: { value: any; depth?: number } = $props();
	const SHOWN = 50;
	const labels: Record<string, Key> = {
		name: 'mcp.name', target: 'mcp.target', mood: 'mcp.moods', genre: 'mcp.genres', songs: 'mcp.songs',
		count: 'mcp.count', known_duration_seconds: 'mcp.duration', unknown_duration_count: 'mcp.unknownDuration',
		reason: 'mcp.reason', confidence: 'mcp.confidence', skipped: 'mcp.skipped', fade: 'mcp.fade',
		sources: 'mcp.collections', members: 'mcp.members', collection_name: 'mcp.collection', collection: 'mcp.collection',
		sync_deletions: 'mcp.syncDeletions', allow_deletions: 'mcp.heldDeletions', follow_deletions: 'mcp.followDeletions',
		interval_minutes: 'mcp.interval', paused: 'mcp.paused', mode: 'mcp.mode', snapshot: 'mcp.snapshot',
		missing: 'mcp.missing', extra: 'mcp.extra', role: 'mcp.role', service: 'mcp.service', kind: 'mcp.kind',
		title: 'mcp.song', artist: 'mcp.artist', album: 'mcp.album', duration: 'mcp.duration', only: 'mcp.only',
		groups: 'mcp.groups', copies: 'mcp.copies', keep: 'mcp.keep', delete: 'mcp.delete', merge: 'mcp.merge',
		changes: 'mcp.changes', selection: 'mcp.collections', color: 'mcp.color', icon: 'mcp.icon',
		delete_guard_min: 'mcp.guardMin', delete_guard_ratio: 'mcp.guardRatio', version_policy: 'mcp.versionPolicy',
		create: 'mcp.create', added: 'mcp.added', removed: 'mcp.removed', failed: 'mcp.failed', summary: 'mcp.summary'
	};
	// ids, internal state and things that are only noise to a person
	const omit = new Set(['note', 'metadata_note', 'id', 'song_id', 'account_id', 'member_id', 'line_id', 'user_id',
		'position', 'created_at', 'updated_at', 'last_run_at', 'last_status', 'last_summary', 'last_seen', 'taken_at',
		'latest', 'last_plan', 'plan', 'run_id', 'isrc', 'collection_id', 'ref_id', 'snapshot_id', 'issue',
		'issue_id', 'fade_id', 'account_status', 'account_message', 'account_connected', 'initialized', 'pending',
		'unmatched', 'covers', 'mine', 'user_name', 'item_count', 'scanned_at', 'error', 'lines', 'included', 'editable',
		'matched_moods', 'number', 'job']);
	const durations = new Set(['known_duration_seconds', 'duration']);
	const names: Record<string, Key> = {
		personal_correction: 'mcp.corrected', excluded: 'mcp.excluded', uncertain: 'mcp.uncertain',
		track_limit: 'mcp.trackLimit', duration_limit: 'mcp.durationLimit', unknown_duration: 'mcp.unknownDuration',
		missing: 'mcp.mode.missing', exact: 'mcp.mode.exact', both: 'role.both', send: 'role.send', receive: 'role.receive',
		liked: 'kind.liked', library: 'kind.library', playlist: 'kind.playlist'
	};
	const shown = (item: unknown) => item !== null && item !== undefined && item !== '' && !(Array.isArray(item) && item.length === 0);
	const text = (v: unknown) => (typeof v === 'number' ? num(v) : names[String(v)] ? t(names[String(v)]) : String(v));
</script>

{#if value !== null && value !== undefined && depth < 8}
	{#if Array.isArray(value)}
		<ul class="list">
			{#each value.slice(0, SHOWN) as item}
				<li><McpPreview value={item} depth={depth + 1} /></li>
			{/each}
		</ul>
		{#if value.length > SHOWN}<p class="t-foot t-2 more">{t('mcp.more', { n: num(value.length - SHOWN) })}</p>{/if}
	{:else if typeof value === 'object'}
		{#if value.title}
			<p class="song"><span class="s-t">{value.title}</span>{#if value.artist}<span class="t-2"> · {value.artist}</span>{/if}</p>
			{#if value.reason}<p class="t-foot t-2">{names[value.reason] ? t(names[value.reason]) : value.reason}</p>{/if}
			{#if typeof value.confidence === 'number'}<p class="t-foot t-2">{t('mcp.confidence')}: {Math.round(value.confidence * 100)} %</p>{/if}
		{:else}
			<dl>
				{#each Object.entries(value) as [key, item]}
					{#if !omit.has(key) && shown(item)}
						<div>
							<dt class="t-foot t-2">{labels[key] ? t(labels[key]) : key.replaceAll('_', ' ')}</dt>
							<dd>
								{#if durations.has(key) && typeof item === 'number'}{mmss(item)}
								{:else if key === 'delete_guard_ratio' && typeof item === 'number'}{Math.round(item * 100)} %
								{:else if key === 'color' && typeof item === 'string'}<span class="swatch" style:background={item}></span>
								{:else}<McpPreview value={item} depth={depth + 1} />{/if}
							</dd>
						</div>
					{/if}
				{/each}
			</dl>
		{/if}
	{:else if typeof value === 'boolean'}
		{value ? t('common.yes') : t('common.no')}
	{:else}
		{text(value)}
	{/if}
{/if}

<style>
	dl {
		margin: 0;
		display: grid;
		gap: 10px;
	}
	dd {
		margin: 2px 0 0;
		overflow-wrap: anywhere;
	}
	.list {
		margin: 0;
		padding-left: 18px;
		display: grid;
		gap: 6px;
	}
	.more {
		margin-top: 6px;
	}
	.song {
		margin: 0;
	}
	.s-t {
		font-weight: 600;
	}
	p {
		margin: 2px 0;
	}
	.swatch {
		display: inline-block;
		width: 16px;
		height: 16px;
		border-radius: 50%;
		vertical-align: middle;
		box-shadow: inset 0 0 0 0.5px rgb(0 0 0 / 0.2);
	}
</style>
