<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '#lib/api.ts';
	import McpPreview from './McpPreview.svelte';
	import { t } from './i18n.svelte.ts';

	let { action }: { action: { id: string; state: string; has_result: boolean } } = $props();
	type Detail = { state: string; preview: unknown; result: unknown };
	let detail = $state<Detail | null>(null);
	let previewOpen = $state(false);
	let resultOpen = $state(false);
	let loading = $state(false);
	let error = $state('');
	let retry = $state(0);
	let loadedRetry = -1;

	$effect(() => {
		const id = action.id;
		const state = action.state;
		const attempt = retry;
		if (!previewOpen && !resultOpen) {
			loading = false;
			return;
		}
		if (untrack(() => detail?.state === state && loadedRetry === attempt)) return;
		const controller = new AbortController();
		loading = true;
		error = '';
		api<Detail>(`/api/mcp/actions/${encodeURIComponent(id)}`, { signal: controller.signal })
			.then((value) => {
				if (controller.signal.aborted) return;
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

<details ontoggle={(event) => (previewOpen = event.currentTarget.open)}>
	<summary>{t('mcp.preview')}</summary>
	{#if previewOpen}
		{#if loading}<p class="t-foot t-2" role="status">{t('mcp.loading')}</p>
		{:else if error}<p class="danger" role="alert">{error}</p><button class="tintc" onclick={() => retry++}>{t('mcp.refresh')}</button>
		{:else if detail}<div class="preview"><McpPreview value={detail.preview} /></div>{/if}
	{/if}
</details>

{#if action.has_result}
	<details ontoggle={(event) => (resultOpen = event.currentTarget.open)}>
		<summary>{t('mcp.result')}</summary>
		{#if resultOpen}
			{#if loading}<p class="t-foot t-2" role="status">{t('mcp.loading')}</p>
			{:else if error}<p class="danger" role="alert">{error}</p><button class="tintc" onclick={() => retry++}>{t('mcp.refresh')}</button>
			{:else if detail}<div class="preview"><McpPreview value={detail.result} /></div>{/if}
		{/if}
	</details>
{/if}

<style>
	.preview { margin-top: 14px; max-height: 420px; overflow: auto; padding-right: 8px; }
	summary { cursor: pointer; color: var(--tint); }
</style>
