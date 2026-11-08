<script lang="ts">
	import { api } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Plan } from '#lib/types.ts';
	import Glyph from './Glyph.svelte';
	import PlanList from './PlanList.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { runFade } from './ctx.ts';
	import { i18n, t } from './i18n.svelte.ts';
	import { tintVars } from './tint.ts';
	import { ui } from './ui.svelte.ts';

	// The result of a preview (or a run that held deletions back), as something you can read and
	// then act on: one clear button to make it so.
	const fade = $derived(ui.planFade ? app.line(ui.planFade) : null);
	let plan = $state<Plan | null>(null);
	let loading = $state(false);

	$effect(() => {
		const id = ui.planFade;
		if (!ui.planOpen || !id) return;
		loading = true;
		api<Plan | null>(`/api/lines/${id}/plan`)
			.then((p) => (plan = p))
			.catch(ui.fail)
			.finally(() => (loading = false));
	});

	const adds = $derived(plan?.plan.members.reduce((n, m) => n + m.add.length, 0) ?? 0);
	const removes = $derived(plan?.plan.members.reduce((n, m) => n + m.remove.length, 0) ?? 0);
	const held = $derived(plan?.plan.held.length ?? 0);
	const preview = $derived(plan?.trigger === 'preview');

	function run() {
		if (!fade) return;
		ui.planOpen = false;
		runFade(fade.id);
	}
	function confirmHeld() {
		if (!fade) return;
		const id = fade.id;
		ui.ask = {
			title: t('plan.confirmQ', { n: held }),
			message: t('plan.confirmMsg'),
			label: t('plan.confirm'),
			run: () => {
				ui.planOpen = false;
				runFade(id, false, true);
			}
		};
	}
</script>

<Sheet bind:open={ui.planOpen} label={t('plan.title')} class="tinted" style={fade ? tintVars(fade.color) : ''} onclosed={() => (plan = null)}>
	{#snippet header()}
		<SheetBar title={preview || !plan ? t('plan.title') : t('plan.lastRun')} leading={{ label: t('common.close'), run: () => (ui.planOpen = false) }} />
	{/snippet}
	{#if plan && fade}
		<div class="wrap">
			<div class="intro">
				<p class="t-title2">{held ? t('plan.heldHead', { n: held }) : adds || removes ? (preview ? t('plan.head') : t('plan.didHead')) : t('plan.none')}</p>
				<p class="t-sub t-2">
					{fade.name} · {new Date(plan.started_at * 1000).toLocaleString(i18n.locale, { dateStyle: 'medium', timeStyle: 'short' })}{preview ? ` · ${t('plan.nothingYetHappened')}` : ''}
				</p>
				{#if adds || removes}
					<div class="chips">
						{#if adds}<span class="pchip add">+{t('n.songs', { n: adds })}</span>{/if}
						{#if removes}<span class="pchip rem">{t('plan.removed', { n: removes })}</span>{/if}
					</div>
				{/if}
			</div>
			<PlanList plan={plan.plan} />
			{#if removes || held}<p class="note t-foot t-2">{t('plan.removeNote')}</p>{/if}
		</div>
		<div class="foot">
			{#if held}
				<button class="pb glass tint lq go" onclick={confirmHeld} disabled={!!fade.job}><Glyph name="check" size={18} weight={2.6} /> {t('plan.confirm')}</button>
			{:else if preview && (adds || removes)}
				<button class="pb glass tint lq go" onclick={run} disabled={!!fade.job}><Glyph name="refresh" size={18} weight={2.4} /> {t('plan.run')}</button>
			{:else}
				<button class="pb glass lq go" onclick={() => (ui.planOpen = false)}>{t('common.done')}</button>
			{/if}
		</div>
	{:else if !loading && ui.planOpen}
		<p class="empty t-sub t-2">{t('plan.empty')}</p>
	{/if}
</Sheet>

<style>
	.wrap {
		padding: 4px 16px 120px;
	}
	.intro {
		display: grid;
		gap: 4px;
		padding: 4px 6px 18px;
	}
	.chips {
		display: flex;
		gap: 8px;
		margin-top: 10px;
	}
	.pchip {
		padding: 6px 12px;
		border-radius: 999px;
		font-size: var(--f-sub);
		font-weight: 700;
	}
	.pchip.add {
		background: color-mix(in srgb, var(--green) 16%, transparent);
		color: var(--green-text);
	}
	.pchip.rem {
		background: color-mix(in srgb, var(--red) 14%, transparent);
		color: var(--red-text);
	}
	.note {
		padding: 12px 8px 0;
	}
	.empty {
		padding: 40px 24px;
		text-align: center;
	}
	.foot {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		padding: 14px 16px max(16px, var(--safe-bottom));
		background: linear-gradient(to top, var(--sheet-solid) 55%, transparent);
	}
	.go {
		width: 100%;
	}
</style>
