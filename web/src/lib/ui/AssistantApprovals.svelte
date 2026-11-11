<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { del, post } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { AssistantWaiting } from '#lib/types.ts';
	import Glyph from './Glyph.svelte';
	import McpActionDetails from './McpActionDetails.svelte';
	import { i18n, t, type Key } from './i18n.svelte.ts';
	import { reducedMotion } from './motion.ts';
	import { ago, ui } from './ui.svelte.ts';

	// What an AI assistant wants to delete waits right here on the home page – the only thing it can't
	// do by itself. One look says what and where; the preview unfolds in place; one tap decides.
	let busy = $state<string | null>(null);
	/** unfolded right away: the one a notification link pointed to */
	let opened = $state<string | null>(null);

	const OPS = ['sync', 'fade_create', 'fade_update', 'fade_delete', 'remove_songs', 'dedupe', 'playlists', 'restore'];
	const operation = (op: string) => (OPS.includes(op) ? t(`mcp.op.${op}` as Key) : op);
	function gist(a: AssistantWaiting) {
		const b = a.brief;
		const parts: string[] = [];
		if (b.songs) parts.push(t('n.songs', { n: b.songs }));
		if (b.playlists) parts.push(t('mcp.nPlaylists', { n: b.playlists }));
		if (b.fade) parts.push(b.fade);
		if (b.collections?.length) parts.push(b.collections.slice(0, 3).join(', ') + (b.collections.length > 3 ? ' …' : ''));
		if (b.taken_at) parts.push(t('mcp.backTo', { date: new Date(b.taken_at * 1000).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'long' }) }));
		return parts.join(' · ');
	}

	// a notification link: show that one, open
	$effect(() => {
		const f = ui.approvalFocus;
		if (!f || !app.assistantWaiting.some((a) => a.id === f)) return;
		untrack(() => {
			opened = f;
			ui.approvalFocus = null;
			tick().then(() => document.getElementById(`approve-${f}`)?.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }));
		});
	});

	async function decide(a: AssistantWaiting, allow: boolean) {
		busy = a.id;
		try {
			if (allow) {
				await post(`/api/mcp/actions/${encodeURIComponent(a.id)}/apply`);
				ui.say(t('mcp.allowed'), 'check');
			} else {
				await del(`/api/mcp/actions/${encodeURIComponent(a.id)}`);
				ui.say(t('mcp.discarded'), 'x', 'info');
			}
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = null;
			await app.loadAssistant();
		}
	}
	/** deleting: say yes once more, like every deletion in Crossfade */
	function allow(a: AssistantWaiting) {
		ui.ask = { title: t('mcp.allowQ', { action: operation(a.operation) }), message: t('mcp.allowMsg'), label: t('mcp.allow'), run: () => decide(a, true) };
	}
</script>

{#if app.assistantWaiting.length}
	<section class="section" aria-labelledby="approvals-h">
		<div class="section-head">
			<h2 id="approvals-h">{t('home.approvals')}</h2>
		</div>
		<div class="cards">
			{#each app.assistantWaiting as a (a.id)}
				<div class="group card" id={`approve-${a.id}`} aria-busy={busy === a.id}>
					<div class="cell">
						<span class="tile-icon" style:background="var(--red)"><Glyph name="trash" size={17} weight={2.2} /></span>
						<span class="grow">
							<span class="b strong">{operation(a.operation)}</span>
							{#if gist(a)}<span class="b t-sub clip">{gist(a)}</span>{/if}
							<span class="b t-foot t-2 clip">{t('mcp.by', { name: a.connection })} · {ago(a.created_at)}</span>
						</span>
					</div>
					<McpActionDetails action={{ id: a.id, state: 'pending', has_result: false }} preview={opened === a.id} />
					<div class="cell choice">
						<button class="pb pb-small glass lq" disabled={busy === a.id} onclick={() => decide(a, false)}>{t('mcp.discard')}</button>
						<button class="pb pb-small glass tint lq" style="--tint-fill: var(--red); --on-tint: #fff" disabled={busy === a.id} onclick={() => allow(a)}>{t('mcp.allow')}</button>
					</div>
				</div>
			{/each}
		</div>
		<p class="section-foot">{t('home.approvalsHint')}</p>
	</section>
{/if}

<style>
	.cards {
		display: grid;
		gap: 12px;
	}
	@media (min-width: 721px) {
		.cards {
			grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
			align-items: start;
		}
	}
	.strong {
		font-weight: 600;
	}
	.choice {
		justify-content: flex-end;
		gap: 10px;
	}
	.choice .pb {
		flex: 1;
	}
</style>
