<script lang="ts">
	import McpPreview from './McpPreview.svelte';
	import { t, type Key } from './i18n.svelte.ts';
	let { value, depth = 0 }: { value: any; depth?: number } = $props();
	const labels: Record<string, Key> = {
		name: 'mcp.name', target: 'mcp.target', mood: 'mcp.moods', genre: 'mcp.genres', songs: 'mcp.songs',
		count: 'mcp.count', known_duration_seconds: 'mcp.seconds', unknown_duration_count: 'mcp.unknownDuration',
		reason: 'mcp.reason', confidence: 'mcp.confidence', skipped: 'mcp.skipped', fade: 'mcp.fade',
		sources: 'mcp.collections', members: 'mcp.collections', collection_name: 'mcp.collection',
		sync_deletions: 'mcp.syncDeletions', allow_deletions: 'mcp.heldDeletions', interval_minutes: 'mcp.interval',
		paused: 'mcp.paused', mode: 'mcp.mode', snapshot: 'mcp.snapshot', missing: 'mcp.missing',
		extra: 'mcp.extra', role: 'mcp.role', service: 'mcp.service', title: 'mcp.song', artist: 'mcp.artist',
		album: 'mcp.album', duration: 'mcp.seconds', only: 'mcp.only', groups: 'mcp.groups', keep: 'mcp.keep'
	};
	const omit = new Set(['note', 'metadata_note', 'id', 'song_id', 'account_id', 'member_id', 'line_id',
		'user_id', 'color', 'icon', 'position', 'created_at', 'updated_at', 'last_run_at', 'last_status',
		'last_summary', 'last_seen', 'taken_at', 'latest', 'last_plan', 'isrc']);
	const names: Record<string, Key> = {
		personal_correction: 'mcp.corrected', excluded: 'mcp.excluded', uncertain: 'mcp.uncertain',
		track_limit: 'mcp.trackLimit', duration_limit: 'mcp.durationLimit', unknown_duration: 'mcp.unknownDuration',
		missing: 'mcp.missing', exact: 'mcp.exact', both: 'role.both', send: 'role.send', receive: 'role.receive',
		liked: 'kind.liked', library: 'kind.library', playlist: 'kind.playlist'
	};
</script>

{#if value !== null && value !== undefined && depth < 10}
	{#if Array.isArray(value)}
		<ul class="preview-list">
			{#each value as item}
				<li><McpPreview value={item} depth={depth + 1} /></li>
			{/each}
		</ul>
	{:else if typeof value === 'object'}
		{#if value.title}
			<p class="song"><strong>{value.title}</strong>{#if value.artist}<span class="t-2"> · {value.artist}</span>{/if}</p>
			{#if value.reason}<p class="t-foot t-2">{names[value.reason] ? t(names[value.reason]) : value.reason}</p>{/if}
			{#if value.confidence !== undefined}<p class="t-foot t-2">{t('mcp.confidence')}: {Math.round(value.confidence * 100)}%</p>{/if}
		{:else}
			<dl>
				{#each Object.entries(value) as [key, item]}
					{#if !omit.has(key) && item !== null && item !== '' && !(Array.isArray(item) && item.length === 0)}
						<div><dt class="t-foot t-2">{labels[key] ? t(labels[key]) : key.replaceAll('_', ' ')}</dt><dd><McpPreview value={item} depth={depth + 1} /></dd></div>
					{/if}
				{/each}
			</dl>
		{/if}
	{:else if typeof value === 'boolean'}
		{value ? t('common.yes') : t('common.no')}
	{:else}
		{names[String(value)] ? t(names[String(value)]) : String(value)}
	{/if}
{/if}

<style>
	dl { margin: 0; display: grid; gap: 8px; }
	dd { margin: 2px 0 0; overflow-wrap: anywhere; }
	.preview-list { margin: 0; padding-left: 18px; display: grid; gap: 8px; }
	.song { margin: 0; }
	p { margin: 3px 0; }
</style>
