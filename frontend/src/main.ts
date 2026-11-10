import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import { installAuth } from './auth-setup'
import { i18n } from './i18n'
import { router } from './router'
import './style.css'

const pinia = createPinia()
const app = createApp(App).use(pinia).use(router).use(i18n)
installAuth(router)
app.mount('#app')
