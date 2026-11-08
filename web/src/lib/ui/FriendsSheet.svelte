<script lang="ts">
	import { del, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Friend, Friends, FriendRequest } from '#lib/types.ts';
	import FadeBadge from './FadeBadge.svelte';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import { userHue } from './artcolor.ts';
	import { fadeIcon } from './fade.ts';
	import { t } from './i18n.svelte.ts';
	import { ago, ui } from './ui.svelte.ts';

	// Friends: ask by username, answer requests, open invitations into their fades.
	// A friendship only shares names; music is shared fade by fade, and only by its owner's choice.
	const f = $derived(app.friends);
	let username = $state('');
	let busy = $state(false);

	$effect(() => {
		if (ui.friendsOpen) app.loadFriends();
	});

	// ranking among friends: on by default, anyone can opt out
	let share = $state(app.user?.share_stats ?? true);
	$effect(() => {
		const want = share;
		if (!app.user || want === app.user.share_stats) return;
		put('/api/auth/me', { share_stats: want })
			.then(() => {
				if (app.user) app.user.share_stats = want;
				app.loadBoard();
			})
			.catch(() => (share = !want));
	});

	async function ask() {
		const name = username.trim();
		if (!name || busy) return;
		busy = true;
		try {
			const before = new Set(app.friends.friends.map((x) => x.id));
			app.friends = await post<Friends>('/api/friends', { username: name });
			username = '';
			const fresh = app.friends.friends.find((x) => !before.has(x.id));
			// they had asked you already: asking back is saying yes
			if (fresh) ui.say(t('friends.accepted', { name: fresh.display_name }), 'users');
			else ui.say(t('friends.sent', { name: app.friends.outgoing[0]?.user.display_name ?? name }), 'users');
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}

	async function accept(r: FriendRequest) {
		try {
			app.friends = await post<Friends>(`/api/friends/requests/${r.id}/accept`);
			ui.say(t('friends.accepted', { name: r.user.display_name }), 'users');
		} catch (e) {
			ui.fail(e);
		}
	}

	async function drop(r: FriendRequest) {
		try {
			app.friends = await del<Friends>(`/api/friends/requests/${r.id}`);
		} catch (e) {
			ui.fail(e);
		}
	}

	function unfriend(p: Friend) {
		ui.ask = {
			title: t('friends.removeQ', { name: p.display_name }),
			message: t('friends.removeMsg'),
			label: t('friends.remove'),
			run: async () => {
				try {
					app.friends = await del<Friends>(`/api/friends/${p.id}`);
					if (p.shared) await app.loadLines();
					ui.say(t('friends.removed', { name: p.display_name }), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}

	const initial = (name: string) => name.trim().charAt(0).toUpperCase() || '?';
</script>

<Sheet bind:open={ui.friendsOpen} label={t('friends.title')}>
	{#snippet header()}
		<SheetBar title={t('friends.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.friendsOpen = false) }} />
	{/snippet}
	<div class="wrap">
		{#if f.invites.length}
			<div class="section">
				<div class="section-head"><span class="caps">{t('friends.invites')}</span></div>
				<ul class="group">
					{#each f.invites as inv (inv.id)}
						<li>
							<button class="cell" style:--inset="70px" onclick={() => ui.join(inv)}>
								<span class="fb"><FadeBadge icon={fadeIcon(inv.fade)} color={inv.fade.color} size={42} /></span>
								<span class="grow">
									<span class="b clip nm">{inv.fade.name}</span>
									<span class="b t-foot t-2 clip">{inv.request ? t('join.waiting', { name: inv.from.display_name }) : `${t('friends.invitedBy', { name: inv.from.display_name })} · ${ago(inv.created_at)}`}</span>
								</span>
								{#if inv.request}<span class="tag">{t('join.asked.short')}</span>{:else}<span class="cell-pill">{t('join.ask')}</span>{/if}
							</button>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if f.incoming.length}
			<div class="section">
				<div class="section-head"><span class="caps">{t('friends.incoming')}</span></div>
				<ul class="group">
					{#each f.incoming as r (r.id)}
						<li>
							<div class="cell" style:--inset="70px">
								<span class="av uav" style:--uh={userHue(String(r.user.id))} aria-hidden="true">{initial(r.user.display_name)}</span>
								<span class="grow">
									<span class="b clip nm">{r.user.display_name}</span>
									<span class="b t-foot t-2 clip">@{r.user.username} · {ago(r.created_at)}</span>
								</span>
								<button class="gbtn round glass lq small" onclick={() => drop(r)} aria-label={t('friends.decline')} title={t('friends.decline')}><Glyph name="x" size={15} weight={2.6} /></button>
								<button class="pb pb-fill pb-small press" onclick={() => accept(r)}>{t('friends.accept')}</button>
							</div>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<div class="section">
			<div class="section-head"><span class="caps">{t('friends.addHead')}</span></div>
			<form
				class="add"
				onsubmit={(e) => {
					e.preventDefault();
					ask();
				}}
			>
				<label class="field grow-field">
					<Glyph name="user" size={16} weight={2.2} />
					<input bind:value={username} placeholder={t('friends.addPh')} aria-label={t('friends.addPh')} autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="60" enterkeyhint="send" />
				</label>
				<button class="pb pb-fill pb-small press" type="submit" disabled={!username.trim() || busy}>{t('friends.ask')}</button>
			</form>
			<p class="section-foot">{t('friends.addHint')}</p>
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('friends.list')}</span></div>
			<ul class="group">
				{#each f.friends as p (p.id)}
					<li>
						<div class="cell" style:--inset="70px">
							<span class="av uav" style:--uh={userHue(String(p.id))} aria-hidden="true">{initial(p.display_name)}</span>
							<span class="grow">
								<span class="b clip nm">{p.display_name}</span>
								<span class="b t-foot t-2 clip">@{p.username}</span>
								<span class="b t-foot t-2 clip">{t('friends.shared', { n: p.shared })}</span>
							</span>
							<button class="text-btn t-sub danger" onclick={() => unfriend(p)}>{t('friends.remove')}</button>
						</div>
					</li>
				{/each}
				{#each f.outgoing as r (r.id)}
					<li>
						<div class="cell" style:--inset="70px">
							<span class="av muted" aria-hidden="true">{initial(r.user.display_name)}</span>
							<span class="grow">
								<span class="b clip nm">{r.user.display_name}</span>
								<span class="b t-foot t-2 clip">@{r.user.username} · {t('friends.waiting')}</span>
							</span>
							<button class="text-btn t-sub" onclick={() => drop(r)}>{t('friends.withdraw')}</button>
						</div>
					</li>
				{/each}
				{#if !f.friends.length && !f.outgoing.length}
					<li><div class="cell"><span class="grow t-2 t-sub">{t('friends.none')}</span></div></li>
				{/if}
			</ul>
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('board.head')}</span></div>
			<div class="group">
				<div class="cell">
					<span class="grow">{t('board.share')}</span>
					<Switch bind:checked={share} label={t('board.share')} />
				</div>
			</div>
			<p class="section-foot">{t('board.shareHint')}</p>
		</div>
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.av {
		display: grid;
		place-items: center;
		flex: none;
		width: 42px;
		height: 42px;
		border-radius: 50%;
		font-weight: 700;
	}
	.av.muted {
		background: var(--fill-3);
		color: var(--label-2);
		box-shadow: none;
	}
	.fb {
		display: grid;
		flex: none;
	}
	.nm {
		font-weight: 600;
	}
	.small {
		width: 34px;
		min-width: 34px;
		height: 34px;
	}
	.add {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.grow-field {
		flex: 1;
		min-width: 0;
	}
</style>
