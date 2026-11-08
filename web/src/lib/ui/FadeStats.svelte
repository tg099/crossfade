<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import type { FadeStats, Line } from '#lib/types.ts';
	import { duration, i18n, num, t } from './i18n.svelte.ts';

	// What this fade has done, from its own sync history only: how complete it is, how much it
	// moved, and where the new songs went. Crossfade never knows what anyone listens to.
	// Per-collection bars: one series, so no legend; values sit at the bar ends, and every bar has
	// a full text label for screen readers.
	let { fade, stats }: { fade: Line; stats: FadeStats } = $props();

	const missing = $derived(Math.max(0, stats.songs - stats.complete));
	// floor: 99.6 % with songs still missing must not read as 100 %
	const pct = $derived(stats.songs ? Math.floor((stats.complete / stats.songs) * 100) : 100);
	const since = $derived(new Date(stats.since * 1000).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: new Date(stats.since * 1000).getFullYear() === new Date().getFullYear() ? undefined : 'numeric' }));
	const rows = $derived(
		fade.members
			.map((m) => ({ m, s: stats.members.find((x) => x.member_id === m.id) ?? { added: 0, removed: 0 } }))
			.filter((r) => r.s.added || r.s.removed)
	);
	const max = $derived(Math.max(1, ...rows.map((r) => r.s.added)));
</script>

<section class="section" aria-labelledby="stats-h">
	<div class="section-head"><h2 id="stats-h">{t('stats.head')}</h2></div>
	<div class="group stats">
		<div class="tiles">
			<p class="tile">
				<span class="t-foot t-2 b">{t('stats.complete')}</span>
				<b class="val">{pct}&thinsp;%</b>
				<span class="t-foot b" class:t-2={!missing} class:warn={missing}>{missing ? t('stats.missing', { n: missing }) : t('stats.allThere')}</span>
			</p>
			<p class="tile">
				<span class="t-foot t-2 b">{t('stats.moved')}</span>
				<b class="val">{num(stats.added)}</b>
				<span class="t-foot t-2 b">{stats.removed ? t('stats.removed', { n: num(stats.removed) }) : t('stats.noRemoved')}</span>
			</p>
			<p class="tile">
				<span class="t-foot t-2 b">{t('stats.syncs')}</span>
				<b class="val">{num(stats.syncs)}</b>
				<span class="t-foot t-2 b">{t('stats.since', { when: since })}</span>
			</p>
			<p class="tile">
				<span class="t-foot t-2 b">{t('stats.saved')}</span>
				<b class="val">{stats.saved_s ? t('stats.savedVal', { d: duration(stats.saved_s) }) : '–'}</b>
				<span class="t-foot t-2 b">{t('stats.savedHint')}</span>
			</p>
		</div>

		{#if rows.length > 1}
			<p class="t-foot t-2 sub">{t('stats.perCollection')}</p>
			<ul class="bars">
				{#each rows as { m, s } (m.id)}
					<li aria-label={t('stats.bar', { name: m.collection_name, added: num(s.added), removed: num(s.removed) })}>
						<span class="name" aria-hidden="true"><ServiceMark service={m.service} size={14} title={false} /><span class="clip">{m.collection_name}</span></span>
						<span class="track" aria-hidden="true"><span class="fill" style:width="{Math.max(s.added ? 2 : 0, (s.added / max) * 100)}%"></span></span>
						<span class="n t-foot" aria-hidden="true">+{num(s.added)}{#if s.removed}<span class="t-2 minus">−{num(s.removed)}</span>{/if}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
	<p class="section-foot" class:warn={stats.failed_30d}>{stats.failed_30d ? t('stats.failed', { n: stats.failed_30d }) : t('stats.hint')}</p>
</section>

<style>
	.stats {
		padding: 14px 16px;
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 8px;
	}
	.tile {
		display: grid;
		align-content: start;
		gap: 1px;
		min-width: 0;
		padding: 10px 12px;
		border-radius: 18px;
		background: var(--fill-3);
	}
	.b {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.val {
		font-size: var(--f-t2);
		font-weight: 800;
		letter-spacing: -0.01em;
		font-variant-numeric: tabular-nums;
		line-height: 1.2;
	}
	.warn {
		color: var(--orange-text);
		font-weight: 600;
	}
	.sub {
		margin: 16px 0 8px;
	}
	.bars {
		display: grid;
		gap: 8px;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.bars li {
		display: grid;
		grid-template-columns: minmax(0, 38%) 1fr auto;
		align-items: center;
		gap: 10px;
	}
	.name {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		font-size: var(--f-sub);
	}
	.clip {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.track {
		height: 8px;
		border-radius: 4px;
		background: var(--fill-3);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		border-radius: 4px;
		background: var(--tint-fill);
	}
	.n {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
	.minus {
		margin-left: 6px;
	}
	@media (min-width: 721px) {
		.tiles {
			grid-template-columns: repeat(4, 1fr);
		}
	}
	@media (max-width: 420px) {
		.tile {
			padding: 9px 10px;
		}
	}
</style>
