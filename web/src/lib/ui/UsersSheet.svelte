<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { app } from '#lib/state.svelte.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { userHue } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';
	import { users } from './users.svelte.ts';

	// Admin only: everyone who can sign in, and the way to add someone.
	$effect(() => {
		if (ui.usersOpen) users.load().catch(ui.fail);
	});
	const edit = (id: number) => {
		ui.userEditing = id;
		ui.userOpen = true;
	};
</script>

<Sheet bind:open={ui.usersOpen} label={t('users.title')}>
	{#snippet header()}
		<SheetBar title={t('users.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.usersOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<div class="section">
			<ul class="group">
				{#each users.list as u (u.id)}
					<li>
						<button class="cell" style:--inset="70px" onclick={() => edit(u.id)}>
							<span class="av uav" style:--uh={userHue(u.id)} aria-hidden="true">{u.display_name.charAt(0).toUpperCase()}</span>
							<span class="grow">
								<span class="b name">
									<span class="clip">{u.display_name}</span>{#if u.id === app.user?.id}<span class="you">({t('users.you')})</span>{/if}
									{#if u.is_admin}<span class="tag accent">{t('users.adminShort')}</span>{/if}
								</span>
								<span class="b t-foot t-2 meta">
									@{u.username} ·
									{u.must_change_password ? t('users.waiting') : t('n.fades', { n: u.lines })}
									{#if u.services.length}<span class="marks">{#each u.services as s}<ServiceMark service={s} size={13} title={false} />{/each}</span>{/if}
								</span>
							</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					</li>
				{/each}
				<li>
					<button class="cell add" style:--inset="70px" onclick={() => edit(0)}>
						<span class="av plus" aria-hidden="true"><Glyph name="plus" size={20} weight={2.6} /></span>
						<span class="grow">{t('users.create')}</span>
					</button>
				</li>
			</ul>
			<p class="section-foot">{t('users.listHint')}</p>
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
	.av.plus {
		background: var(--fill-3);
		color: var(--label);
		box-shadow: none;
	}
	.name {
		display: flex;
		align-items: center;
		gap: 6px;
		font-weight: 600;
	}
	.you {
		color: var(--label-2);
		font-weight: 400;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.marks {
		display: inline-flex;
		gap: 3px;
		margin-left: 4px;
	}
	.add .grow {
		font-weight: 600;
	}
</style>
