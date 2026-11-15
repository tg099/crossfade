<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { num, t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Putting a fade's likes in the order they were liked. Crossfade reads the collections first (a
	// short job of the fade), then this shows per liked collection how many songs stand elsewhere;
	// the person picks which to put in order.
	let picked = $state<Set<number>>(new Set());
	let busy = $state(false);
	const rows = $derived(ui.reorder?.rows ?? null);
	// only one collection out of order: that one. Several: the person picks (moving many likes of a
	// service that keeps its own dates may not be what they want)
	$effect(() => {
		const out = (rows ?? []).filter((r) => r.moves);
		picked = new Set(out.length === 1 ? [out[0].member_id] : []);
	});
	// one song: unliked, liked again a little more than a second after the one before
	const minutes = (n: number) => Math.max(1, Math.round((n * 1.6) / 60));
	const total = $derived((rows ?? []).filter((r) => picked.has(r.member_id)).reduce((a, r) => a + r.moves, 0));
	const playlists = $derived((rows ?? []).some((r) => r.kind === 'playlist'));
	const youtube = $derived((rows ?? []).some((r) => r.service === 'ytm' && r.moves && picked.has(r.member_id)));

	function toggle(id: number) {
		const next = new Set(picked);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		picked = next;
	}
	async function go() {
		const r = ui.reorder;
		if (!r || !total) return;
		busy = true;
		try {
			await post(`/api/lines/${r.line_id}/reorder`, { preview: false, member_ids: [...picked] });
			ui.say(t('reorder.started'), 'updown');
			ui.reorderOpen = false;
			app.reloadLine(r.line_id);
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}
</script>

<Sheet bind:open={ui.reorderOpen} label={t('reorder.title')} onclosed={() => (ui.reorder = null)}>
	{#snippet header()}
		<SheetBar title={t('reorder.title')} leading={{ label: t('common.close'), run: () => (ui.reorderOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{t('reorder.intro')}</p>
		{#if !rows}
			<p class="intro t-sub t-2 center"><span class="spin" aria-hidden="true"></span> {t('reorder.reading')}</p>
		{:else if !rows.some((r) => r.moves)}
			<p class="intro t-sub center">{t('reorder.nothing')}</p>
		{:else}
			<div class="section">
				<ul class="group">
					{#each rows as r (r.member_id)}
						<li>
							<button class="cell" style:--inset="58px" onclick={() => toggle(r.member_id)} disabled={!r.moves} aria-pressed={picked.has(r.member_id)}>
								<ServiceMark service={r.service} size={30} />
								<span class="grow">
									<span class="b clip">{r.name}</span>
									<span class="b t-foot t-2" title={SERVICE_NAME[r.service]}>
										{r.moves ? t('reorder.moves', { n: num(r.moves), min: minutes(r.moves) }) : t('reorder.inOrder')}
									</span>
								</span>
								{#if r.moves}
									<span class="pick" class:on={picked.has(r.member_id)} aria-hidden="true">{#if picked.has(r.member_id)}<Glyph name="check" size={14} weight={3} />{/if}</span>
								{/if}
							</button>
						</li>
					{/each}
				</ul>
				<div class="actions">
					<button class="pb glass tint lq" onclick={go} disabled={busy || !total}>
						<Glyph name="updown" size={18} weight={2.4} />
						{total ? t('reorder.go', { n: num(total) }) : t('reorder.pick')}
					</button>
				</div>
				<p class="section-foot">{t('reorder.foot')}{playlists ? ` ${t('reorder.playlist')}` : ''}{youtube ? ` ${t('reorder.youtube')}` : ''}</p>
			</div>
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 22px;
	}
	.center {
		text-align: center;
	}
	.spin {
		display: inline-block;
		width: 14px;
		height: 14px;
		margin-right: 6px;
		vertical-align: -2px;
		border: 2px solid var(--label-3);
		border-top-color: var(--tint);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.spin {
			animation-duration: 2.4s;
		}
	}
	.pick {
		display: grid;
		flex: none;
		place-items: center;
		width: 24px;
		height: 24px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--label-3);
		color: var(--on-tint);
	}
	.pick.on {
		background: var(--tint);
		box-shadow: none;
	}
	.actions {
		display: flex;
		justify-content: center;
		margin-top: 14px;
	}
</style>
