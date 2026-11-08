<script lang="ts">
	import { app } from '#lib/state.svelte.ts';
	import FadeBadge from './FadeBadge.svelte';
	import { fadeIcon } from './fade.ts';
	import { duration, i18n, t } from './i18n.svelte.ts';
	import { tintVars } from './tint.ts';
	import { nav } from './ui.svelte.ts';

	// "Your week": one headline number, a quiet 7-day column chart, two facts. All of it from the
	// sync history (what arrived), never from listening: Crossfade doesn't know that.
	// Single series → no legend; the title names it. The value is labelled on the peak only; every
	// column shows its value on hover/tap/focus, and a table carries all values for screen readers.
	const week = $derived(app.activity?.week ?? { days: [0, 0, 0, 0, 0, 0, 0], total: 0, prev_total: 0, saved_s: 0, top_artist: null, top_line: null });
	const max = $derived(Math.max(1, ...week.days));
	const peak = $derived(week.days.indexOf(Math.max(...week.days)));
	const days = $derived(
		week.days.map((_, i) => {
			const d = new Date();
			d.setDate(d.getDate() - (week.days.length - 1 - i));
			return {
				short: i === week.days.length - 1 ? t('week.today') : d.toLocaleDateString(i18n.locale, { weekday: 'short' }).replace('.', ''),
				long: d.toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' })
			};
		})
	);
	const top = $derived(week.top_line ? app.line(week.top_line) : null);
	// compared with the 7 days before; says nothing while both weeks are empty
	const trend = $derived.by(() => {
		const prev = week.prev_total ?? 0;
		if (!week.total && !prev) return null;
		const d = week.total - prev;
		return { d, text: d > 0 ? t('week.more', { n: d }) : d < 0 ? t('week.fewer', { n: -d }) : t('week.same') };
	});
	let active = $state<number | null>(null);
	const PLOT = 64; // px
</script>

<section class="week glass strong" aria-labelledby="week-h">
	<div class="head">
		<h2 id="week-h" class="t-sub">{t('week.title')}</h2>
		<p class="hero"><span class="num">{week.total}</span> <span class="unit">{t('week.unit')}</span></p>
		<p class="t-foot t-2">{t('week.sub')}</p>
		{#if trend}<p class="t-foot trend" class:up={trend.d > 0}>{trend.text}</p>{/if}
		{#if week.saved_s}<p class="t-foot t-2">{t('week.saved', { d: duration(week.saved_s) })}</p>{/if}
	</div>

	{#if !week.total}
		<p class="t-sub t-2 quiet">{t('week.quiet')}</p>
	{:else}
	<div class="chart" role="group" aria-label={t('week.chart')} onpointerleave={() => (active = null)}>
		<span class="base" aria-hidden="true"></span>
		{#each week.days as v, i}
			<button
				type="button"
				class="col"
				class:on={active === i}
				aria-label={t('week.bar', { day: days[i].long, n: v })}
				onpointerenter={() => (active = i)}
				onfocus={() => (active = i)}
				onblur={() => (active = null)}
				onclick={() => (active = active === i ? null : i)}
			>
				<span class="slot" style:height="{PLOT}px">
					{#if v && (active === i || (active === null && i === peak))}
						<span class="val" style:bottom="{(v / max) * PLOT + 6}px">{v}</span>
					{/if}
					<span class="bar" style:height="{Math.max(3, (v / max) * PLOT)}px"></span>
				</span>
				<span class="day" class:today={i === days.length - 1}>{days[i].short}</span>
			</button>
		{/each}
	</div>

	<table class="sr">
		<caption>{t('week.chart')}</caption>
		<tbody>
			{#each week.days as v, i}<tr><th scope="row">{days[i].long}</th><td>{v}</td></tr>{/each}
		</tbody>
	</table>

	{/if}
	{#if week.total}
	<div class="facts">
		{#if week.top_artist}
			<p><span class="t-foot t-2 b">{t('week.topArtist')}</span><b class="clip b">{week.top_artist.name}</b><span class="t-foot t-2">{t('n.songs', { n: week.top_artist.songs })}</span></p>
		{/if}
		{#if top}
			<button class="fact-line tinted lq" style={tintVars(top.color)} onclick={() => nav.openFade(top.id)}>
				<span class="t-foot t-2 b">{t('week.topFade')}</span>
				<span class="ln"><FadeBadge icon={fadeIcon(top)} color={top.color} size={18} /><b>{top.name}</b></span>
			</button>
		{/if}
	</div>
	{/if}
</section>

<style>
	.week {
		margin: 0 16px 34px;
		padding: 18px 18px 16px;
		border-radius: 30px;
	}
	.quiet {
		margin-top: 10px;
	}
	.head h2 {
		font-weight: 700;
		color: var(--label-2);
	}
	.hero {
		margin-top: 2px;
		line-height: 1.05;
	}
	.num {
		font-size: 2.6rem;
		font-weight: 800;
		letter-spacing: -0.02em;
		font-variant-numeric: tabular-nums;
	}
	.unit {
		font-size: var(--f-t3);
		font-weight: 700;
	}
	.trend {
		margin-top: 4px;
		font-weight: 600;
		color: var(--label-2);
	}
	.trend.up {
		color: var(--label);
	}
	.chart {
		position: relative;
		display: grid;
		grid-template-columns: repeat(7, 1fr);
		margin: 30px -4px 0;
	}
	/* recessive hairline baseline */
	.base {
		position: absolute;
		left: 4px;
		right: 4px;
		top: 86px;
		border-top: 1px solid var(--sep);
	}
	.col {
		display: grid;
		justify-items: center;
		gap: 6px;
		padding: 0 2px;
		border-radius: 12px;
	}
	.col:focus-visible {
		outline-offset: 0;
	}
	.slot {
		position: relative;
		display: flex;
		align-items: flex-end;
		justify-content: center;
		width: 100%;
	}
	/* thin column, 4px rounded data end, square at the baseline */
	.bar {
		width: min(18px, 60%);
		border-radius: 4px 4px 0 0;
		background: var(--chart);
		opacity: 0.85;
		transition: opacity 150ms;
	}
	.col.on .bar {
		opacity: 1;
	}
	.chart:has(.col.on) .col:not(.on) .bar {
		opacity: 0.45;
	}
	.val {
		position: absolute;
		left: 50%;
		translate: -50% 0;
		font-size: var(--f-foot);
		font-weight: 700;
		color: var(--label);
		font-variant-numeric: tabular-nums;
	}
	.day {
		font-size: var(--f-cap);
		color: var(--label-2);
	}
	.day.today {
		color: var(--label);
		font-weight: 700;
	}
	.facts {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
		margin-top: 16px;
	}
	.facts > * {
		display: grid;
		gap: 2px;
		padding: 10px 12px;
		border-radius: 18px;
		background: var(--fill-3);
		text-align: left;
		min-width: 0;
	}
	.b {
		display: block;
	}
	.ln {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
	}
	.ln b {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	@media (min-width: 721px) {
		.week {
			margin-left: 36px;
			margin-right: 36px;
			max-width: 560px;
		}
	}
</style>
