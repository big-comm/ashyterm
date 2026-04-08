# Referência: Como o Google Chrome Agrupa Abas (Tab Groups)

> **Fonte:** blog.google, suporte oficial Chrome, observação direta do comportamento  
> **Data:** 2026-04-07  

---

## 1. Criação de Grupos

### Como criar
- **Clique direito** em qualquer aba → opção **"Adicionar guia ao novo grupo"**
- Também disponível: **"Adicionar guia ao grupo existente"** → mostra lista dos grupos abertos
- Ao criar, o grupo recebe automaticamente uma **cor** da paleta e o cursor foca no **campo de nome** do label

### Seleção múltipla
- `Ctrl+Click` em várias abas → clique direito → **"Adicionar guias ao novo grupo"**
- `Shift+Click` seleciona intervalo contíguo de abas

---

## 2. Visual do Grupo

### Label (Chip) do grupo
- É um **retângulo arredondado** com **fundo na cor do grupo**
- Fica **à esquerda** das abas do grupo, na **mesma linha da barra de abas**
- Contém o **nome do grupo** (texto) — pode ser texto, emoji, ou vazio
- Se o nome estiver vazio, o chip é apenas um **círculo/pílula** colorido sem texto (um "dot")
- **Altura** do chip é a mesma das abas — alinhamento vertical perfeito

### Abas agrupadas
- As abas do grupo ficam **contíguas** (sem espaço entre elas) logo após o chip
- Cada aba do grupo tem uma **barra/underline colorida na parte inferior** na cor do grupo
- A underline tem ~3px de espessura e é **contínua** entre as abas (parece uma linha só)
- O restante da aba mantém aparência normal (mesma altura, ícone, título, botão fechar)
- Abas agrupadas **não** têm fundo colorido — apenas a underline inferior

### Espaçamento
- **Entre chip e primeira aba do grupo:** 0px (grudados)
- **Entre abas do mesmo grupo:** 0px (grudadas, underline contínua)
- **Entre última aba do grupo e próxima aba/grupo:** espaçamento normal (4-6px)

---

## 3. Interações

### Clique no chip
- **Clique esquerdo** no chip → **colapsa/expande** o grupo
  - **Colapsado:** todas as abas do grupo ficam ocultas, só o chip aparece
  - **Expandido:** as abas voltam a aparecer à direita do chip
  - Transição suave (animação de slide)

### Clique direito no chip
- Menu de contexto com:
  - **"Novo grupo de guias neste grupo"** (abre nova aba já no grupo)
  - **"Desagrupar"** (remove agrupamento, abas ficam soltas)
  - **"Fechar grupo"** (fecha TODAS as abas do grupo)
  - **Paleta de cores** (8 cores disponíveis para trocar)
  - **Campo de nome** editável inline

### Arrastar (Drag & Drop)
- Arrastar uma aba para **dentro da área do grupo** (entre chip e última aba) → adiciona ao grupo
- Arrastar uma aba para **fora do grupo** → remove do grupo (fica solta)
- Arrastar o **chip** → move o grupo inteiro (todas as abas se movem junto)
- Arrastar entre abas de um mesmo grupo → reordena dentro do grupo

### Clique direito em aba agrupada
- Menu normal da aba + opcão **"Remover do grupo"**
- Também: **"Mover guia para outro grupo"** → lista grupos existentes

---

## 4. Cores da Paleta

O Chrome oferece **8 cores** fixas para grupos:

| Nome     | Cor aproximada |
|----------|---------------|
| Grey     | #5F6368       |
| Blue     | #1A73E8       |
| Red      | #D93025       |
| Yellow   | #F9AB00       |
| Green    | #188038       |
| Pink     | #D01884       |
| Purple   | #7627BB       |
| Cyan     | #007B83       |

- A primeira cor é atribuída automaticamente
- As cores **rotacionam** — próximo grupo pega a próxima cor da paleta
- O usuário pode trocar a cor a qualquer momento pelo menu do chip

---

## 5. Colapsar/Expandir

- Clicar no chip **colapsa** → abas desaparecem, chip vira "pílula" compacta
- Clicar de novo **expande** → abas reaparecem
- Quando colapsado:
  - O chip mantém a cor e o nome
  - Tooltip mostra quantas abas estão no grupo
  - O espaço das abas é liberado na barra de abas

---

## 6. Persistência

- Grupos são **salvos** quando o Chrome é fechado
- Ao reabrir, os grupos são restaurados com:
  - Nome, cor, estado (colapsado/expandido)
  - Ordem das abas dentro do grupo
  - Posição do grupo na barra

---

## 7. Comportamentos Especiais

- **Nova aba** aberta por link de uma aba agrupada → automaticamente adicionada ao mesmo grupo
- **Arrastar aba para fora da janela** → cria nova janela, aba sai do grupo
- **Fechar última aba** de um grupo → grupo é automaticamente deletado
- **Renomear** → clique direto no texto do chip ou via menu de contexto
- **Undo** (Ctrl+Z) após fechar grupo → restaura o grupo inteiro

---

## 8. Resumo do Layout Visual

```
Barra de Abas:
┌─────────┬──────────┬──────────┬───┬──────────────┬──────────┬──────────┐
│ ■ Dev   │ Tab A    │ Tab B    │   │ ■ Prod       │ Tab D    │ Tab F    │
│ (azul)  │ ________ │ ________ │   │ (vermelho)   │ ________ │ (solta)  │
│ chip    │ underline│ underline│   │ chip         │ underline│          │
└─────────┴──────────┴──────────┘   └──────────────┴──────────┘──────────┘
  grupo 1 (expandido)                 grupo 2 (expandido)       sem grupo

Grupo colapsado:
┌─────────┬───┬──────────────┬──────────┬──────────┐
│ ■ Dev   │   │ ■ Prod       │ Tab D    │ Tab F    │
│ (azul)  │   │ (vermelho)   │ ________ │ (solta)  │
│ pílula  │   │ chip         │ underline│          │
└─────────┘   └──────────────┴──────────┘──────────┘
  grupo 1                     grupo 2       sem grupo
  (colapsado)                 (expandido)
```

---

## 9. O Que Implementar no AshyTerm

### Prioridade Alta (Chrome Core)
1. Chip com fundo colorido, nome editável, click para colapsar/expandir
2. Underline colorida contínua nas abas agrupadas (3px, mesma cor do grupo)
3. Menu de contexto do chip: renomear, trocar cor, desagrupar, fechar grupo
4. Menu de contexto da aba: "Novo grupo", "Adicionar ao grupo X", "Remover do grupo"
5. Colapso: chip vira pílula, abas somem; expandir restaura
6. Persistência: salvar/restaurar grupos com estado

### Prioridade Média (UX refinado)
7. Seleção múltipla de abas (Ctrl+Click) para agrupar várias de uma vez
8. Drag & drop: arrastar aba para dentro/fora do grupo
9. Animação suave de colapso/expansão
10. Atalho de teclado para criar grupo (Ctrl+Shift+G já implementado)

### Prioridade Baixa (Nice to have)
11. Tooltip com contagem de abas no chip colapsado
12. Renomear inline direto no chip (clique duplo)
