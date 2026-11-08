<script lang="ts">
	import { app } from '#lib/state.svelte.ts';
	import { userHue } from './artcolor.ts';
	import { num, t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Ranking among friends: songs each person brought in over the last 7 days (from their
	// collections, added elsewhere by Crossfade). Only a number per person; who opts out is
	// left out and doesn't see it either. Bars share one scale, the value sits at the end.
	const people = $derived(app.board?.people ?? []);
	const max = $derived(Math.max(1, ...people.map((p) => p.songs)));
	// equal counts share a rank
	const ranks = $derived(people.map((p) => 1 + people.filter((q) => q.songs > p.songs).length));
	const initial = (name: string) => name.trim().charAt(0).toUpperCase() || '?';
</script>

{#if people.length > 1}
	<section class="section" aria-labelledby="board-h">
		<div class="section-head">
			<h2 id="board-h">{t('board.title')}</h2>
			<button class="gbtn glass lq" onclick={() => ((ui.accountOpen = true), (ui.friendsOpen = true))}>{t('friends.title')}</button>
		</div>
		<ol class="group board" aria-describedby="board-sub">
			<li class="sub t-foot t-2" id="board-sub" aria-hidden="true">{t('board.sub')}</li>
			{#each people as p, i (p.id)}
				<li class="row" class:me={p.me} aria-label={t('board.row', { rank: ranks[i], name: p.me ? t('board.you') : p.display_name, n: p.songs })}>
					<span class="rank t-foot" aria-hidden="true">{ranks[i]}</span>
					<span class="av uav" style:--uh={userHue(String(p.id))} aria-hidden="true">{initial(p.display_name)}</span>
					<span class="body" aria-hidden="true">
						<span class="line">
							<span class="nm clip">{p.me ? t('board.you') : p.display_name}</span>
							<b class="n">{num(p.songs)}</b>
						</span>
						<span class="track"><span class="fill" style:width="{Math.max(p.songs ? 2 : 0, (p.songs / max) * 100)}%"></span></span>
					</span>
				</li>
			{/each}
		</ol>
		<p class="section-foot">{t('board.hint')}</p>
	</section>
{/if}

<style>
	.board {
		padding: 12px 16px 14px;
		display: grid;
		gap: 12px;
	}
	.sub {
		margin-bottom: -2px;
	}
	.row {
		display: grid;
		grid-template-columns: 18px 32px 1fr;
		align-items: center;
		gap: 10px;
	}
	.rank {
		font-weight: 700;
		color: var(--label-2);
		text-align: center;
		font-variant-numeric: tabular-nums;
	}
	.av {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 50%;
		font-size: var(--f-foot);
		font-weight: 700;
	}
	.body {
		display: grid;
		gap: 5px;
		min-width: 0;
	}
	.line {
		display: flex;
		align-items: baseline;
		gap: 8px;
		min-width: 0;
	}
	.nm {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.me .nm {
		font-weight: 700;
	}
	.n {
		font-variant-numeric: tabular-nums;
	}
	.track {
		height: 6px;
		border-radius: 3px;
		background: var(--fill-3);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		border-radius: 3px;
		background: var(--chart);
		opacity: 0.55;
	}
	.me .fill {
		opacity: 1;
	}
</style>
