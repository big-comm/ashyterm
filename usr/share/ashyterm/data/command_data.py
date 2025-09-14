# ashyterm/data/command_data.py
from ..utils.translation_utils import _

# A estrutura de dados foi alterada para agrupar variações de comandos.
NATIVE_COMMANDS = [
    {
        "category": _("Navegação e Listagem de Arquivos"),
        "command": "ls",
        "general_description": _("Lista o conteúdo de um diretório."),
        "variations": [
            {
                "name": "ls -l",
                "description": _(
                    "Usa um formato de lista longa, mostrando permissões, dono, tamanho e data."
                ),
            },
            {
                "name": "ls -a",
                "description": _(
                    "Mostra todos os arquivos, incluindo os ocultos (que começam com '.')."
                ),
            },
            {
                "name": "ls -lh",
                "description": _(
                    "Formato longo com tamanhos de arquivo legíveis para humanos (KB, MB, GB)."
                ),
            },
            {
                "name": "ls -t",
                "description": _(
                    "Ordena os arquivos por data de modificação, os mais recentes primeiro."
                ),
            },
        ],
    },
    {
        "category": _("Navegação e Listagem de Arquivos"),
        "command": "cd",
        "general_description": _("Muda o diretório de trabalho atual."),
        "variations": [
            {
                "name": "cd /caminho/para/diretorio",
                "description": _("Navega para um caminho absoluto específico."),
            },
            {
                "name": "cd ..",
                "description": _("Sobe um nível, indo para o diretório pai."),
            },
            {
                "name": "cd ~",
                "description": _(
                    "Vai diretamente para o seu diretório 'home'. O mesmo que 'cd' sem argumentos."
                ),
            },
            {
                "name": "cd -",
                "description": _("Volta para o último diretório em que você estava."),
            },
        ],
    },
    {
        "category": _("Manipulação de Arquivos"),
        "command": "cp",
        "general_description": _("Copia arquivos ou diretórios."),
        "variations": [
            {
                "name": "cp arquivo_origem arquivo_destino",
                "description": _("Copia um único arquivo."),
            },
            {
                "name": "cp -r diretorio_origem/ diretorio_destino/",
                "description": _(
                    "Copia um diretório e todo o seu conteúdo recursivamente."
                ),
            },
            {
                "name": "cp -v arquivo_origem arquivo_destino",
                "description": _("Modo 'verbose', mostra o que está sendo copiado."),
            },
        ],
    },
    {
        "category": _("Manipulação de Arquivos"),
        "command": "mv",
        "general_description": _("Move ou renomeia arquivos e diretórios."),
        "variations": [
            {
                "name": "mv nome_antigo nome_novo",
                "description": _("Renomeia um arquivo ou diretório no local atual."),
            },
            {
                "name": "mv arquivo.txt /novo/diretorio/",
                "description": _("Move um arquivo para um novo local."),
            },
        ],
    },
    {
        "category": _("Manipulação de Arquivos"),
        "command": "rm",
        "general_description": _(
            "Remove (apaga) arquivos ou diretórios. CUIDADO: esta ação é permanente."
        ),
        "variations": [
            {"name": "rm arquivo.txt", "description": _("Remove um único arquivo.")},
            {
                "name": "rm -r diretorio/",
                "description": _("Remove um diretório e todo o seu conteúdo."),
            },
            {
                "name": "rm -i arquivo.txt",
                "description": _("Modo interativo, pede confirmação antes de remover."),
            },
            {
                "name": "rm -f arquivo.txt",
                "description": _(
                    "Força a remoção sem pedir confirmação (usar com cautela)."
                ),
            },
        ],
    },
    {
        "category": _("Busca e Filtragem"),
        "command": "grep",
        "general_description": _(
            "Busca por um padrão de texto dentro de arquivos ou saídas de outros comandos."
        ),
        "variations": [
            {
                "name": "grep 'palavra' arquivo.txt",
                "description": _(
                    "Encontra e exibe todas as linhas que contêm 'palavra' no arquivo."
                ),
            },
            {
                "name": "grep -i 'palavra' arquivo.txt",
                "description": _(
                    "Busca ignorando a diferença entre maiúsculas e minúsculas."
                ),
            },
            {
                "name": "grep -r 'palavra' .",
                "description": _(
                    "Busca recursivamente por 'palavra' em todos os arquivos do diretório atual."
                ),
            },
            {
                "name": "ls -l | grep 'txt'",
                "description": _(
                    "Filtra a saída do comando 'ls -l' para mostrar apenas linhas que contêm 'txt'."
                ),
            },
        ],
    },
    {
        "category": _("Controle de Fluxo (Shell Script)"),
        "command": "if",
        "general_description": _("Executa blocos de comandos condicionalmente."),
        "variations": [
            {
                "name": "if [ -f 'arquivo.txt' ]; then ... fi",
                "description": _("Testa se um arquivo existe e é um arquivo regular."),
            },
            {
                "name": "if [ -d 'diretorio' ]; then ... fi",
                "description": _("Testa se um caminho existe e é um diretório."),
            },
            {
                "name": 'if [ "$VAR" -eq 5 ]; then ... fi',
                "description": _("Testa se uma variável numérica é igual a 5."),
            },
        ],
    },
    {
        "category": _("Controle de Fluxo (Shell Script)"),
        "command": "for",
        "general_description": _(
            "Cria um laço (loop) que itera sobre uma lista de itens."
        ),
        "variations": [
            {
                "name": "for i in 1 2 3; do ... done",
                "description": _("Itera sobre uma lista explícita de números."),
            },
            {
                "name": "for f in *.txt; do ... done",
                "description": _(
                    "Itera sobre todos os arquivos que terminam com .txt no diretório."
                ),
            },
        ],
    },
    {
        "category": _("Controle de Fluxo (Shell Script)"),
        "command": "while",
        "general_description": _(
            "Cria um laço (loop) que continua enquanto uma condição for verdadeira."
        ),
        "variations": [
            {
                "name": "while [ $COUNT -lt 5 ]; do ... done",
                "description": _(
                    "Executa o bloco de código enquanto a variável COUNT for menor que 5."
                ),
            },
        ],
    },
    {
        "category": _("Controle de Fluxo (Shell Script)"),
        "command": "until",
        "general_description": _(
            "Cria um laço (loop) que continua enquanto uma condição for falsa."
        ),
        "variations": [
            {
                "name": "until [ $COUNT -eq 0 ]; do ... done",
                "description": _(
                    "Executa o bloco de código até que a variável COUNT seja igual a 0."
                ),
            },
        ],
    },
    {
        "category": _("Entrada do Usuário (Shell Script)"),
        "command": "read",
        "general_description": _(
            "Lê uma linha da entrada padrão e a armazena em uma variável."
        ),
        "variations": [
            {
                "name": "read NOME",
                "description": _(
                    "Espera o usuário digitar algo e pressionar Enter, salvando na variável NOME."
                ),
            },
            {
                "name": "read -p 'Digite seu nome: ' NOME",
                "description": _(
                    "Mostra uma mensagem (prompt) para o usuário antes de esperar a entrada."
                ),
            },
        ],
    },
    {
        "category": _("Expansão de Parâmetros"),
        "command": "Expansão de Variáveis",
        "general_description": _(
            "Mecanismos do shell para manipular o valor de variáveis."
        ),
        "variations": [
            {
                "name": "${VAR}",
                "description": _(
                    "Substitui pelo valor da variável VAR. É o mesmo que $VAR, mas mais seguro."
                ),
            },
            {
                "name": "${VAR:-default}",
                "description": _(
                    "Usa 'default' se VAR não estiver definida. A variável VAR não é alterada."
                ),
            },
            {
                "name": "${VAR:=default}",
                "description": _(
                    "Usa e atribui 'default' a VAR se ela não estiver definida."
                ),
            },
            {
                "name": "${#VAR}",
                "description": _(
                    "Retorna o número de caracteres no valor da variável VAR."
                ),
            },
        ],
    },
]
