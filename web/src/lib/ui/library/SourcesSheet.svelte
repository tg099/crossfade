<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { CatalogSource, Service } from '#lib/types.ts';
	import Sheet from '../Sheet.svelte';
	import SheetBar from '../SheetBar.svelte';
	import Switch from '../Switch.svelte';
	import { num, t } from '../i18n.svelte.ts';
	import { ago, ui } from '../ui.svelte.ts';
	import { catalog as c, kindRank, SERVICES } from './catalog.svelte.ts';

	// Which collections the library reads. Switching one off only hides it here.
	const groups = $derived(
		SERVICES.map((svc) => ({
			svc,
			list: (c.data?.sources ?? []).filter((s) => s.service === svc).sort((a, b) => kindRank(a) - kindRank(b) || c.collator.compare(a.name, b.name))
		})).filter((g) => g.list.length)
	);
	async function set(s: CatalogSource, on: boolean) {
		try {
			await put(`/api/catalog/sources/${s.id}`, { included: on });
			await c.load(true);
		} catch (e) {
			ui.fail(e);
		}
	}
	const connectedNotScanned = $derived(app.connected.filter((s) => !groups.some((g) => g.svc === s.service)).map((s) => s.service as Service));
</script>

<Sheet bind:open={c.sourcesOpen} label={t('lib.sources')}>
	{#snippet header()}
		<SheetBar title={t('lib.sources')} confirm={{ label: t('common.done'), run: () => (c.sourcesOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{t('lib.sourcesIntro')}</p>
		{#each groups as g (g.svc)}
			<div class="section">
				<div class="section-head"><span class="caps head"><ServiceMark service={g.svc} size={16} /> {SERVICE_NAME[g.svc]}</span></div>
				<div class="group">
					{#each g.list as s (s.id)}
						<div class="cell">
							<span class="grow">
								<span class="b clip">{s.name}</span>
								<span class="b t-foot clip" class:t-2={!s.error} class:danger={!!s.error}>
									{s.error ?? (s.scanned_at ? `${t('n.songs', { n: num(s.item_count ?? 0) })} · ${ago(s.scanned_at)}` : t('lib.notScanned'))}
								</span>
							</span>
							<Switch bind:checked={() => s.included, (v) => set(s, v)} label={s.name} />
						</div>
					{/each}
				</div>
			</div>
		{/each}
		{#if connectedNotScanned.length}
			<p class="section-foot pre">{t('lib.scanToSee', { services: connectedNotScanned.map((s) => SERVICE_NAME[s]).join(', ') })}</p>
		{/if}
		<div class="section">
			<div class="group">
				<button class="cell center" onclick={() => (c.scan(), (c.sourcesOpen = false))} disabled={!!app.catalogJob || !app.connected.length}>
					<span class="grow tintc">{t('lib.rescan')}</span>
				</button>
			</div>
			<p class="section-foot">{t('lib.scanHint')}</p>
		</div>
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 20px;
	}
	.head {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.pre {
		margin: -8px 16px 22px;
	}
</style>
