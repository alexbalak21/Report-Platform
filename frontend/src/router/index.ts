import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Role } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import BlocksView from '@/views/BlocksView.vue'
import HomeView from '@/views/HomeView.vue'
import LoginView from '@/views/LoginView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import ProfilesView from '@/views/ProfilesView.vue'
import TablesView from '@/views/TablesView.vue'
import UploadView from '@/views/UploadView.vue'
import UsersView from '@/views/UsersView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    /** i18n key; routes with a `nav` entry appear in the main menu. */
    nav?: string
    public?: boolean
    /** Minimum role (viewer < operator < designer < admin). Defaults to any signed-in user. */
    minRole?: Role
  }
}

// Routes mirror docs/software_dev_plan.md section 9.1. Pages are filled in phase by phase.
export const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
  { path: '/', name: 'home', component: HomeView, meta: { nav: 'nav.home' } },
  {
    path: '/data/upload',
    name: 'upload',
    component: UploadView,
    meta: { nav: 'nav.upload', minRole: 'operator' },
  },
  {
    path: '/data/tables',
    name: 'tables',
    component: TablesView,
    meta: { nav: 'nav.tables' },
  },
  {
    path: '/data/profiles',
    name: 'profiles',
    component: ProfilesView,
    meta: { nav: 'nav.profiles', minRole: 'designer' },
  },
  {
    path: '/blocks',
    name: 'blocks',
    component: BlocksView,
    meta: { nav: 'nav.blocks', minRole: 'designer' },
  },
  {
    path: '/blocks/new',
    name: 'block-new',
    component: () => import('@/views/BlockEditorView.vue'), // keeps CodeMirror out of the main bundle
    meta: { minRole: 'designer' },
  },
  {
    path: '/blocks/:id/edit',
    name: 'block-edit',
    component: () => import('@/views/BlockEditorView.vue'),
    meta: { minRole: 'designer' },
  },
  {
    path: '/templates',
    name: 'templates',
    component: PlaceholderView,
    meta: { nav: 'nav.templates', minRole: 'designer' },
  },
  { path: '/reports', name: 'reports', component: PlaceholderView, meta: { nav: 'nav.reports' } },
  {
    path: '/admin/users',
    name: 'users',
    component: UsersView,
    meta: { nav: 'nav.users', minRole: 'admin' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

/** Only same-site absolute paths; blocks open redirects such as `//evil.com` or `https://...`. */
export function safeRedirect(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    ? value
    : '/'
}

export function createAppRouter(history = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    await auth.init() // restores the session from the refresh cookie once
    if (to.meta.public) {
      return auth.isAuthenticated && to.name === 'login' ? safeRedirect(to.query.redirect) : true
    }
    if (!auth.isAuthenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.minRole && !auth.hasRole(to.meta.minRole)) return { name: 'home' }
    return true
  })

  return router
}

export const router = createAppRouter()
