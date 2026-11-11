<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '#lib/api.ts';
	import Glyph from './Glyph.svelte';
	import McpPreview from './McpPreview.svelte';
	import { t } from './i18n.svelte.ts';

	// Preview and result of one proposal, as rows of its group that unfold. The (possibly large)
	// details are only fetched once a row is opened, and again when the proposal's state changes.
	let { action, preview: startOpen = false }: { action: { id: string; state: string; has_result: boolean }; preview?: boolean } = $props();
	type Detail = { state: string; preview: unknown; result: unknown };
	let detail = $state<Detail | null>(null);
	let previewOpen = $state(untrack(() => startOpen));
	let resultOpen = $state(false);
	let loading = $state(false);
	let error = $state('');
	let retry = $state(0);
	let loadedRetry = -1;

	$effect(() => {
		const id = action.id;
		const state = action.state;
		const attempt = retry;
		if (!previewOpen && !resultOpen) return;
		if (untrack(() => detail?.state === state && loadedRetry === attempt)) return;
		const controller = new AbortController();
		loading = true;
		error = '';
		api<Detail>(`/api/mcp/actions/${encodeURIComponent(id)}`, { signal: controller.signal })
			.then((value) => {
				detail = value;
				loadedRetry = attempt;
			})
			.catch((e: Error) => {
				if (!controller.signal.aborted) error = e.message;
			})
			.finally(() => {
				if (!controller.signal.aborted) loading = false;
			});
		return () => controller.abort();
	});
</script>

{#snippet body(value: unknown)}
	<div class="detail">
		{#if loading && !detail}<p class="t-foot t-2" role="status">{t('mcp.loading')}</p>
		{:else if error}<p class="t-foot danger" role="alert">{error}</p><button class="text-btn t-foot" onclick={() => retry++}>{t('common.retry')}</button>
		{:else if detail}<McpPreview {value} />{/if}
	</div>
{/snippet}

<button class="cell" onclick={() => (previewOpen = !previewOpen)} aria-expanded={previewOpen}>
	<span class="grow tintc">{t('mcp.preview')}</span>
	<span class="chev" class:turned={previewOpen}><Glyph name="chevronRight" size={16} weight={2.4} /></span>
</button>
{#if previewOpen}{@render body(detail?.preview)}{/if}

{#if action.has_result}
	<button class="cell" onclick={() => (resultOpen = !resultOpen)} aria-expanded={resultOpen}>
		<span class="grow tintc">{t('mcp.result')}</span>
		<span class="chev" class:turned={resultOpen}><Glyph name="chevronRight" size={16} weight={2.4} /></span>
	</button>
	{#if resultOpen}{@render body(detail?.result)}{/if}
{/if}

<style>
	.detail {
		max-height: 420px;
		overflow: auto;
		padding: 2px 18px 14px;
		font-size: var(--f-sub);
		overscroll-behavior: contain;
	}
	.chev {
		transition: transform 220ms ease;
	}
	.turned {
		transform: rotate(90deg);
	}
	@media (prefers-reduced-motion: reduce) {
		.chev {
			transition: none;
		}
	}
</style>
