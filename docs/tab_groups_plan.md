# Planejamento: Sistema de Grupos de Abas (Tab Groups) — AshyTerm

> **Status:** Planejamento  
> **Data:** 2026-03-30  
> **Autor:** Copilot + Ruscher

---

## 1. Resumo Executivo

Implementar grupos de abas nomeados e coloridos, similares aos de navegadores modernos (Chrome/Edge), permitindo que o usuário organize terminais logicamente. Os grupos devem ser persistentes entre sessões.

---

## 2. Arquitetura Atual (Resumo)

### Componentes-chave envolvidos

| Arquivo | Responsabilidade |
|---|---|
| `terminal/tabs.py` → `TabManager` | Gerencia abas, `ViewStack`, tab bar custom (`Gtk.Box` horizontal), criação/fechamento, DnD manual, menu de contexto |
| `terminal/manager.py` → `TerminalManager` | Ciclo de vida de terminais (local, SSH, SFTP), eventos, settings |
| `terminal/registry.py` → `TerminalRegistry` | Registro central de terminais ativos com metadados |
| `terminal/pane_manager.py` → `PaneManager` | Splits horizontais/verticais dentro de uma aba |
| `state/window_state.py` → `WindowStateManager` | Serialização/restauração do layout (`session_state.json`) |
| `sessions/models.py` → `SessionItem` | Modelo GObject de sessão (nome, host, cor, etc.) |
| `window.py` → `CommTerminalWindow` | Janela principal, ações, atalhos, integração geral |
| `data/styles/window.css` | Estilos das abas (`.custom-tab-button`, etc.) |

### Estrutura atual da tab bar

```
CommTerminalWindow
 └─ scrolled_tab_bar (Gtk.ScrolledWindow)
     └─ tab_bar_box (Gtk.Box horizontal, spacing=4)
         ├─ tab_widget_1 (Gtk.Box .custom-tab-button)
         ├─ tab_widget_2 (Gtk.Box .custom-tab-button)
         └─ ...
```

- Cada `tab_widget` é um `Gtk.Box` com ícone + label + botão fechar.
- `TabManager.tabs` → `List[Gtk.Box]` (ordem visual).
- `TabManager.pages` → `WeakKeyDictionary[Gtk.Box, Adw.ViewStackPage]`.
- `TabManager.view_stack` → `Adw.ViewStack` contém o conteúdo de cada aba.
- Não existe conceito de agrupamento — tudo é lista plana.

### Persistência atual (`session_state.json`)

```json
{
  "_schema_version": 1,
  "tabs": [
    { "type": "terminal", "session_type": "local", "session_name": "Local", "working_dir": "/home/user" },
    { "type": "paned", "orientation": "horizontal", "start_child": {...}, "end_child": {...} }
  ]
}
```

---

## 3. Design Proposto

### 3.1 Modelo de Dados

```python
# Novo arquivo: src/ashyterm/terminal/tab_groups.py

@dataclass
class TabGroup:
    id: str                        # UUID
    name: str                      # Nome editável pelo usuário
    color: str                     # Cor CSS (ex: "#f28b82")
    is_collapsed: bool = False     # Grupo recolhido?
    tab_ids: list[str] = field(default_factory=list)  # IDs de tab_widgets ordenados
```

### 3.2 Estrutura Visual Proposta

```
tab_bar_box (Gtk.Box horizontal)
 ├─ [group_chip_A] (Gtk.Box .tab-group-chip)   ← clicável: expande/recolhe
 │   ├─ color_dot (Gtk.DrawingArea)
 │   ├─ label "Servidores"
 │   └─ chevron_icon (expand/collapse)
 ├─ tab_widget_1 (Gtk.Box .custom-tab-button .in-group .group-A)
 ├─ tab_widget_2 (Gtk.Box .custom-tab-button .in-group .group-A)
 ├─ [group_chip_B]
 │   └─ ...
 ├─ tab_widget_5 (Gtk.Box .custom-tab-button .in-group .group-B)
 ├─ tab_widget_6 (Gtk.Box .custom-tab-button)   ← sem grupo
 └─ ...
```

**Decisão arquitetural:** Em vez de `Gtk.Notebook` aninhado (complexo, conflita com `ViewStack`), manter a **tab bar plana** e inserir **"chips"** de grupo como widgets separados no `tab_bar_box`. Os chips atuam como cabeçalhos visuais. Abas do grupo ficam contíguas após o chip. Isso minimiza a refatoração e mantém o `Adw.ViewStack` inalterado.

### 3.3 Persistência (Schema v2)

```json
{
  "_schema_version": 2,
  "groups": [
    {
      "id": "uuid-1",
      "name": "Servidores",
      "color": "#f28b82",
      "is_collapsed": false
    }
  ],
  "tabs": [
    {
      "type": "terminal",
      "session_type": "ssh",
      "session_name": "prod-server",
      "working_dir": null,
      "group_id": "uuid-1"
    },
    {
      "type": "terminal",
      "session_type": "local",
      "session_name": "Local",
      "working_dir": "/home/user",
      "group_id": null
    }
  ],
  "active_tab_id": null
}
```

- **Migração v1→v2:** Se `groups` ausente, tratar todos os tabs como `group_id: null`.
- Campo `group_id` (nullable) adicionado a cada tab serializado.
- Lista `groups` no top-level define os metadados de cada grupo.

---

## 4. Arquivos a Criar/Modificar

### Novos

| Arquivo | Conteúdo |
|---|---|
| `src/ashyterm/terminal/tab_groups.py` | `TabGroup` (dataclass), `TabGroupManager` (CRUD de grupos, estado, serialização) |
| `src/ashyterm/data/styles/tab_groups.css` | Estilos do chip de grupo, borda colorida, indicador de cor |

### Modificados

| Arquivo | Mudanças |
|---|---|
| `terminal/tabs.py` (`TabManager`) | Integrar `TabGroupManager`, renderizar chips no `tab_bar_box`, estender menu de contexto, lógica de colapsar/expandir, reordenação com grupos |
| `state/window_state.py` (`WindowStateManager`) | Schema v2, serializar `group_id` por aba, serializar lista de grupos, migração v1→v2 |
| `window.py` (`CommTerminalWindow`) | Registrar novos atalhos de teclado, ações GAction para grupos |
| `data/styles/window.css` | Classe `.in-group` para abas agrupadas (borda inferior colorida) |
| `settings/config.py` | (Opcional) Setting para habilitar/desabilitar grupos |

---

## 5. Fases de Implementação

### Fase 1 — Modelo e CRUD (sem UI)
**Objetivo:** Criar e testar a camada de dados isoladamente.

- [ ] Criar `tab_groups.py` com `TabGroup` (dataclass) e `TabGroupManager`
  - Métodos: `create_group()`, `delete_group()`, `rename_group()`, `set_color()`, `add_tab()`, `remove_tab()`, `toggle_collapsed()`, `get_group_for_tab()`, `reorder_tab_in_group()`, `to_dict()`, `from_dict()`
- [ ] Testes unitários para `TabGroupManager`
- [ ] Estender `WindowStateManager` com schema v2 + migração v1→v2
- [ ] Testes para migração de schema

### Fase 2 — Widget Chip de Grupo + Renderização
**Objetivo:** Mostrar chips de grupo na tab bar e agrupar abas visualmente.

- [ ] Criar widget `_create_group_chip(group: TabGroup) -> Gtk.Box`
  - Cor (dot ou borda), nome (label editável inline), chevron expandir/colapsar
- [ ] Criar CSS `tab_groups.css` com estilos para `.tab-group-chip`, `.in-group`, `.group-collapsed`
- [ ] Método `_rebuild_tab_bar_with_groups()` em `TabManager`:
  - Remove todos os filhos de `tab_bar_box`
  - Insere chips e abas na ordem correta (grupos primeiro com suas abas, depois não agrupadas)
  - Usa `group.is_collapsed` para esconder/mostrar abas do grupo
- [ ] Conectar chip click → `toggle_collapsed()` → rebuild

### Fase 3 — Menu de Contexto e Ações
**Objetivo:** Permitir gestão de grupos via menu de contexto.

- [ ] Estender `_on_tab_right_click` com seção "Grupo":
  - "Adicionar ao Novo Grupo" → cria grupo com a aba selecionada
  - "Mover para Grupo ▶" → submenu listando grupos existentes
  - "Remover do Grupo" → desagrupa a aba (visível apenas se aba está num grupo)
- [ ] Click direito no chip do grupo:
  - "Renomear Grupo"
  - "Cor do Grupo…"
  - "Desagrupar Todas"
  - "Fechar Grupo"
  - "Mover Grupo para Nova Janela" (se `on_detach_tab_requested` suportar multiplos)
- [ ] Criar `GAction`s para cada operação de grupo em `CommTerminalWindow._setup_actions()`

### Fase 4 — Atalhos de Teclado
**Objetivo:** Atalhos para ações frequentes.

| Atalho | Ação |
|---|---|
| `Ctrl+Shift+G` | Adicionar aba atual a novo grupo |
| `Ctrl+Shift+U` | Remover aba atual do grupo |
| `Alt+Shift+Left/Right` | Mover aba entre grupos / reordenar dentro do grupo |

- [ ] Registrar em `_setup_keyboard_shortcuts()`
- [ ] Testar conflitos com atalhos existentes

### Fase 5 — Drag and Drop entre Grupos
**Objetivo:** Mover abas entre grupos via arrastar-e-soltar.

- [ ] Estender a lógica de move existente (`_start_tab_move`, `_perform_tab_move`):
  - Ao soltar sobre um chip de grupo → adicionar ao grupo
  - Ao soltar entre abas de outro grupo → mover para aquele grupo na posição correta
  - Ao soltar fora de qualquer grupo → desagrupar
- [ ] Visual feedback durante drag: highlight do chip/grupo destino
- [ ] **Nota:** DnD manual via `_tab_being_moved` já existe. Estender, não reescrever.

### Fase 6 — Persistência e Restauração
**Objetivo:** Grupos sobrevivem ao reinício.

- [ ] `save_session_state()`: incluir `group_id` em cada tab + lista `groups`
- [ ] `restore_session_state()`: recriar `TabGroupManager` a partir do state, recriar abas com grupo
- [ ] Testar cenários:
  - Salvar com grupos → restaurar → layout idêntico
  - State v1 (sem grupos) → migrar → abrir sem erros
  - Grupo com aba SSH que falha ao reconectar

### Fase 7 — Polish e Edge Cases
**Objetivo:** Robustecer e polir.

- [ ] Fechar última aba de um grupo → deletar grupo automaticamente
- [ ] Criar aba nova → herdar grupo da aba ativa (configurável)
- [ ] Split terminal → manter no mesmo grupo da aba pai
- [ ] Duplicar aba → manter no mesmo grupo
- [ ] Detach tab → remover do grupo
- [ ] Performance com 50+ abas: garantir que rebuild é O(n) e não acumula widgets
- [ ] Acessibilidade: labels ARIA nos chips, navegação por teclado nos grupos

---

## 6. Considerações Técnicas

### ViewStack e Grupos
O `Adw.ViewStack` permanece inalterado — ele gerencia o conteúdo (area do terminal). Os grupos afetam apenas a **tab bar visual** (`tab_bar_box`). Isso é fundamental para minimizar o impacto.

### Armazenamento do `group_id` na tab
Cada `tab_widget` (Gtk.Box) já recebe atributos custom (`label_widget`, `session_item`, etc.). Adicionaremos `tab_widget.group_id: Optional[str]`.

### CSS Approach
```css
/* Chip do grupo */
.tab-group-chip {
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.85em;
    font-weight: 600;
    margin-right: 2px;
}

/* Aba dentro de um grupo — borda inferior colorida */
.custom-tab-button.in-group {
    border-bottom: 2px solid var(--group-color);
    border-radius: 10px 10px 4px 4px;
}

/* Grupo recolhido — esconder abas filhas */
/* Controlado programaticamente via widget.set_visible(False) */
```

### Wayland/X11
O DnD atual já foi adaptado para Wayland (motion controllers desabilitados por freezes). O sistema de move manual via click é compatível. A extensão para grupos segue o mesmo padrão.

---

## 7. Riscos e Mitigações

| Risco | Impacto | Mitigação |
|---|---|---|
| Rebuild frequente da tab bar causa flicker | Médio | Usar `tab_bar_box.freeze_child_notify()` / batch updates |
| Conflito de DnD com lógica existente | Alto | Estender `_perform_tab_move`, não substituir |
| Schema migration corrompe state | Alto | Backup automático antes de migrar; fallback para v1 |
| Label editável inline no chip causa bugs de foco | Baixo | Usar dialog (Adw.MessageDialog) para renomear, não inline editing |
| Performance com muitos grupos | Baixo | Lazy rebuild; grupos colapsados = widgets hidden, não destruídos |

---

## 8. Testes Necessários

| Teste | Tipo | Fase |
|---|---|---|
| CRUD de TabGroup/TabGroupManager | Unitário | 1 |
| Serialização/deserialização de grupos | Unitário | 1 |
| Migração schema v1→v2 | Unitário | 1 |
| Rebuild visual da tab bar com grupos | Integração | 2 |
| Menu de contexto com opções de grupo | Manual/UI | 3 |
| Persistência entre sessões | Integração | 6 |
| Fechar grupo → fechar todas as abas | Integração | 3 |
| Edge case: último tab do grupo | Unitário | 7 |

---

## 9. Dependências

- Nenhuma dependência nova de pacote.
- Usa apenas GTK4, libadwaita, Python stdlib (`uuid`, `dataclasses`, `json`).

---

## 10. Definição de Pronto (DoD)

- [ ] Grupos podem ser criados, renomeados, recoloridos e deletados
- [ ] Abas podem ser movidas entre grupos e desagrupadas
- [ ] Grupos podem ser expandidos/recolhidos
- [ ] Layout de grupos persiste entre sessões
- [ ] Migração automática de sessões existentes (v1→v2)
- [ ] Atalhos de teclado funcionais
- [ ] CSS integrado ao tema Adwaita
- [ ] Todos os testes passando
- [ ] Sem regressão em abas sem grupo (comportamento padrão inalterado)
