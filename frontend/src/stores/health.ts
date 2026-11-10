import { defineStore } from 'pinia'
import { ref } from 'vue'
import { errorMessage } from '@/api/client'
import { fetchHealth, type ServiceHealth } from '@/api/health'

export const useHealthStore = defineStore('health', () => {
  const services = ref<ServiceHealth[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      services.value = (await fetchHealth()).services
    } catch (e) {
      services.value = []
      error.value = errorMessage(e)
    } finally {
      loading.value = false
    }
  }

  return { services, loading, error, refresh }
})
