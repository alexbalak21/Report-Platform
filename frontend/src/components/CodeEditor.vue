<script setup lang="ts">
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { basicSetup } from 'codemirror'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string
  language: 'html' | 'css'
  readonly?: boolean
  label: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const host = ref<HTMLDivElement | null>(null)
let view: EditorView | null = null

function extensions() {
  return [
    basicSetup,
    props.language === 'html' ? html() : css(),
    EditorView.lineWrapping,
    EditorState.readOnly.of(Boolean(props.readonly)),
    EditorView.editable.of(!props.readonly),
    EditorView.contentAttributes.of({ 'aria-label': props.label }),
    EditorView.updateListener.of((u) => {
      if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
    }),
  ]
}

onMounted(() => {
  if (!host.value) return
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({ doc: props.modelValue, extensions: extensions() }),
  })
})

// Parent changed the text (load a version, reload after a conflict): mirror it in the editor.
watch(
  () => props.modelValue,
  (value) => {
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
    }
  },
)

// Read-only can flip after a load (a default block); rebuild the state with the new flag.
watch(
  () => props.readonly,
  () => view?.setState(EditorState.create({ doc: props.modelValue, extensions: extensions() })),
)

onBeforeUnmount(() => view?.destroy())

/** Inserts text where the cursor is (replacing the selection) and keeps the focus in the editor. */
function insertAtCursor(text: string) {
  if (!view || props.readonly) return
  const { from, to } = view.state.selection.main
  view.dispatch({
    changes: { from, to, insert: text },
    selection: { anchor: from + text.length },
  })
  view.focus()
}

defineExpose({ insertAtCursor })
</script>

<template>
  <div
    ref="host"
    class="code-editor overflow-hidden rounded-md border border-surface-border bg-white text-sm"
    :data-language="language"
  />
</template>

<style>
.code-editor .cm-editor {
  max-height: 22rem;
}
.code-editor .cm-editor.cm-focused {
  outline: 2px solid theme('colors.brand.500');
  outline-offset: -1px;
}
.code-editor .cm-scroller {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
