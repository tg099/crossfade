// Admin only: everyone who can sign in. Shared by the user list and the user sheet.
import { api } from '#lib/api.ts';
import type { AdminUser } from '#lib/types.ts';

class Users {
	list = $state<AdminUser[]>([]);
	loaded = $state(false);
	async load() {
		this.list = await api<AdminUser[]>('/api/admin/users');
		this.loaded = true;
	}
}
export const users = new Users();
