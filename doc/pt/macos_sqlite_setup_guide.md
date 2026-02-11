# Configuração no macOS — Guia Completo

Guia passo a passo para configurar o Crypto FIFO Tracker de raiz no macOS.

---

## Pré-requisitos

- macOS 12 (Monterey) ou posterior
- Acesso ao Terminal
- ~500 MB de espaço em disco

---

## Passo 1 — Instalar Homebrew

O Homebrew é o gestor de pacotes para macOS.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Após a instalação, seguir as instruções para adicionar o Homebrew ao PATH:
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Verificar:
```bash
brew --version
```

---

## Passo 2 — Instalar Python

```bash
brew install python@3.13
```

Verificar:
```bash
python3 --version   # deve mostrar 3.11+
pip3 --version
```

O SQLite já vem incluído no Python — não é necessário instalar separadamente:
```bash
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

---

## Passo 3 — Clonar ou descarregar o projeto

```bash
# Opção A: clonar com git
git clone https://github.com/YOUR_USERNAME/crypto-fifo-tracker.git
cd crypto-fifo-tracker

# Opção B: descarregar e extrair o ZIP
# Descarregar de GitHub → Code → Download ZIP
# Extrair e navegar até à pasta
cd crypto-fifo-tracker
```

---

## Passo 4 — Executar o setup automático

```bash
chmod +x setup.sh
./setup.sh
```

O script:
1. Verifica a versão do Python (necessita 3.11+)
2. Cria a estrutura de diretórios (`calculators/`, `importers/`, `data/`, `data/reports/`, `doc/`, `data/backups/`, `tests/`)
3. Cria um ambiente virtual (`venv/`)
4. Instala os pacotes Python (`pandas`, `pytz`, `openpyxl`, `requests`)
5. Inicializa a base de dados SQLite vazia com o esquema correto

Se preferir não usar ambiente virtual:
```bash
./setup.sh --no-venv
```

---

## Passo 5 — Ativar o ambiente virtual

```bash
source venv/bin/activate
```

Deverá ver `(venv)` no início da linha de comandos. Para desativar:
```bash
deactivate
```

**Importante**: ativar o ambiente virtual sempre que trabalhar com o projeto.

---

## Passo 6 — Verificar a instalação

```bash
# Verificar que os pacotes estão instalados
python3 -c "import pandas; print(f'pandas {pandas.__version__}')"
python3 -c "import openpyxl; print(f'openpyxl {openpyxl.__version__}')"

# Verificar a base de dados
python3 -c "
import sqlite3
conn = sqlite3.connect('data/crypto_fifo.db')
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print(f'Tabelas: {[t[0] for t in tables]}')
conn.close()
"
```

Resultado esperado:
```
pandas 2.x.x
openpyxl 3.x.x
Tabelas: ['transactions', 'fifo_lots', 'sale_lot_matches']
```

---

## Passo 7 — Configurar

Editar `config.py` se necessário:
- **País**: `COUNTRY = "PT"` (já configurado por defeito para Portugal)
- **Base de dados**: `DATABASE_PATH` (por defeito `data/crypto_fifo.db`)
- **Exchanges**: adicionar/remover exchanges em `EXCHANGE_COUNTRIES`

---

## Passo 8 — Preparar os dados

1. **Descarregar CSVs** das exchanges (ver [guia de download](how_reports.md))
2. **Colocar** os ficheiros na pasta `data/`
3. **Descarregar** o ficheiro de taxas BCE (`eurusd.csv`) e colocá-lo em `data/`

---

## Pacotes Python necessários

Já instalados pelo `setup.sh`, mas para referência:

- **pandas** — manipulação de dados CSV
- **pytz** — gestão de fusos horários
- **openpyxl** — criação de ficheiros Excel (.xlsx)
- **requests** — chamadas API (CryptoCompare para preços históricos)

Para instalar manualmente:
```bash
pip install -r requirements.txt
```

---

## Resolução de problemas

### "command not found: python3"

```bash
brew install python@3.13
```

### "No module named pandas"

```bash
source venv/bin/activate   # certificar-se que o venv está ativado
pip install -r requirements.txt
```

### "database is locked"

Certificar-se que não tem outra sessão SQLite aberta na mesma base de dados. Fechar quaisquer gestores de BD GUI antes de executar scripts.

### Problemas de permissão no setup.sh

```bash
chmod +x setup.sh
./setup.sh
```

### SQLite não encontrado (raro)

O SQLite está incluído no Python. Se faltar:
```bash
brew install sqlite3
```

---

## Estrutura após configuração

```
crypto-fifo-tracker/
├── config.py
├── setup.sh
├── venv/                   ← Ambiente virtual Python
├── requirements.txt
├── calculators/
├── importers/
├── data/                   ← TODOS os dados pessoais (excluídos do git)
│   ├── crypto_fifo.db      ← Criado pelo setup.sh
│   ├── reports/             ← Relatórios gerados
│   ├── backups/             ← Cópias de segurança
│   └── supporting_documents/
├── doc/
└── tests/
```

---

## Próximos passos

1. Colocar os ficheiros CSV em `data/`
2. Seguir o [Início Rápido](quickstart.md) para importar, calcular e gerar relatórios
3. Consultar o [Guia Completo](crypto_fifo_guide.md) para detalhes sobre cada fase
