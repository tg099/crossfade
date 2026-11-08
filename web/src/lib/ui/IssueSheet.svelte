<script lang="ts">
	import { SERVICE_NAME } from '#lib/format.ts';
	import type { Issue } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import HearButton from './HearButton.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { t } from './i18n.svelte.ts';
	import { ignoreIssue, researchIssue, resolveIssue, sameLength } from './issues.ts';
	import { tintVars } from './tint.ts';
	import { mmss } from './ui.svelte.ts';

	// One song that wasn't found for sure: listen to the suggestions, pick one, or paste a link.
	let {
		open = $bindable(false),
		fadeId,
		color,
		issue = $bindable(null),
		onchange
	}: { open?: boolean; fadeId: number; color: string; issue: Issue | null; onchange: () => void } = $props();

	let choice = $state(0);
	let link = $state('');
	let busy = $state(false);
	$effect(() => {
		issue?.id;
		choice = 0;
		link = '';
	});
	const cands = $derived(issue?.data.candidates ?? []);

	async function take() {
		if (!issue || busy) return;
		const target = link.trim() || cands[choice]?.id;
		if (!target) return;
		busy = true;
		if (await resolveIssue(fadeId, issue, target)) {
			open = false;
			onchange();
		}
		busy = false;
	}
	async function search() {
		if (!issue || busy) return;
		busy = true;
		const c = await researchIssue(fadeId, issue);
		if (c && issue) issue = { ...issue, data: { ...issue.data, candidates: c } };
		busy = false;
	}
	async function ignore() {
		if (!issue || busy) return;
		busy = true;
		if (await ignoreIssue(fadeId, issue)) {
			open = false;
			onchange();
		}
		busy = false;
	}
</script>

<Sheet bind:open label={t('issue.pick')} class="tinted" style={tintVars(color)}>
	{#snippet header()}
		<SheetBar
			title={t('issue.pick')}
			leading={{ label: t('common.cancel'), run: () => (open = false) }}
			confirm={{ label: t('review.take'), run: take, disabled: busy || (!link.trim() && !cands.length) }}
		/>
	{/snippet}
	{#if issue}
		{@const tr = issue.data.track}
		<div class="pick">
			<div class="orig">
				<span class="orig-art">
					<Cover src={tr.artwork} seed={tr.id} size={88} track={tr} />
					<span class="orig-hear"><HearButton track={tr} size={36} onArt /></span>
				</span>
				<div>
					<p class="t-title3">{tr.title}</p>
					<p class="t-sub t-2">{tr.artist}{tr.album ? ` · ${tr.album}` : ''}{tr.duration ? ` · ${mmss(tr.duration)}` : ''}</p>
					<p class="t-foot t-2 q">{issue.data.reason ? `${issue.data.reason} · ` : ''}{t('issue.question', { service: SERVICE_NAME[issue.service] })}</p>
				</div>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('issue.foundIn', { service: `${SERVICE_NAME[issue.service]} · ${issue.collection_name}` })}</span></div>
				{#if cands.length}
					<div class="group" role="radiogroup" aria-label={t('issue.version')}>
						{#each cands as c, ci (c.id)}
							<div class="cell cand" style:--inset="72px">
								<HearButton track={c} size={44} />
								<button class="cand-pick" role="radio" aria-checked={choice === ci && !link.trim()} onclick={() => ((choice = ci), (link = ''))}>
									<span class="grow">
										<span class="clip b">{c.title}</span>
										<span class="t-foot t-2 clip b">{c.artist}{c.album ? ` · ${c.album}` : ''}{c.duration ? ` · ${mmss(c.duration)}` : ''}</span>
										<span class="tags">
											{#if c.exact}<span class="tag good">{t('issue.exact')}</span>{:else if c.notes?.length}<span class="tag warn">{c.notes.join(', ')}</span>{/if}
											{#if sameLength(c, tr)}<span class="tag">{t('review.sameLength')}</span>{/if}
											{#if c.score != null}<span class="tag">{Math.round(c.score * 100)} %</span>{/if}
										</span>
									</span>
									<span class="ck" aria-hidden="true">{#if choice === ci && !link.trim()}<Glyph name="check" size={20} weight={2.6} />{/if}</span>
								</button>
							</div>
						{/each}
					</div>
					<p class="section-foot">{t('issue.hearHint')}</p>
				{:else}
					<p class="none t-sub t-2">{t('issue.noCandidates')}</p>
				{/if}
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('issue.byLink')}</span></div>
				<div class="group">
					<label class="cell">
						<Glyph name="link" size={20} weight={2} />
						<input class="grow in" bind:value={link} placeholder={t('issue.linkPh', { service: SERVICE_NAME[issue.service] })} autocomplete="off" spellcheck="false" />
					</label>
				</div>
				<p class="section-foot">{t('issue.linkHint')}</p>
			</div>

			<div class="section">
				<div class="group">
					<button class="cell" onclick={search} disabled={busy}><Glyph name="refresh" size={20} weight={2} /><span class="grow">{t('issue.searchAgain')}</span></button>
					<button class="cell" onclick={ignore} disabled={busy}><Glyph name="x" size={20} weight={2.2} /><span class="grow bad">{t('issue.ignoreHere')}</span></button>
				</div>
			</div>
		</div>
	{/if}
</Sheet>

<style>
	.pick {
		padding: 8px 0 40px;
	}
	.orig {
		display: flex;
		gap: 16px;
		align-items: center;
		padding: 8px 20px 24px;
	}
	.orig-art {
		position: relative;
		flex: none;
	}
	.orig-hear {
		position: absolute;
		right: -8px;
		bottom: -8px;
	}
	.q {
		margin-top: 6px;
	}
	.b {
		display: block;
	}
	.cand-pick {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: 12px;
		text-align: left;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 4px;
	}
	.ck {
		width: 22px;
		color: var(--tint);
	}
	.none {
		padding: 4px 8px;
	}
	.in {
		border: 0;
		outline: none;
		background: none;
		color: var(--label);
		font: inherit;
		min-width: 0;
	}
	.bad {
		color: var(--red-text);
	}
</style>
