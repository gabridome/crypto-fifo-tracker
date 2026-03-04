# Crypto FIFO Tracker

Sistema open-source de rastreamento FIFO para mais-valias de criptoativos, concebido para a declaração de IRS em Portugal. Adaptável a outras jurisdições.

**[English documentation](../en/README.md)**

## Funcionalidades

- **Interface web** — wizard guiado (Collect → Import → Status → FIFO → Reports)
- **Base de dados SQLite** — ficheiro único, sem servidor, portátil
- **Método FIFO** — obrigatório pela legislação fiscal portuguesa
- **Multi-exchange** — importadores dedicados para Binance, Coinbase, Coinbase Prime, Bitstamp, Bitfinex, Kraken, Mt.Gox, TRT, Wirex, Revolut, Bybit e importador genérico CSV
- **Moeda EUR** — conversão USD→EUR via taxas históricas oficiais do BCE
- **Relatórios IRS** — Excel com Anexo G1 (isentos, ≥365 dias) e Anexo J (tributáveis, <365 dias)
- **Agregação diária** — conforme exigido pela Autoridade Tributária
- **Preparado para multi-país** — regras fiscais separadas em `config.py`

## Experimentar a demo

Para explorar a aplicação com dados de exemplo realistas, sem ficheiros reais das exchanges:

```bash
git clone https://github.com/gabridome/crypto-fifo-tracker.git
cd crypto-fifo-tracker

# Criar ambiente virtual e instalar dependências
python3 -m venv venv
source venv/bin/activate
pip install flask pandas pytz openpyxl requests

# Gerar 900 transações demo e criar a base de dados demo
python3 generate_demo_data.py
python3 setup_demo.py

# Lançar a interface web com a base de dados demo
FIFO_DB=data/DEMO_crypto_fifo.db python3 web/app.py
# Abrir http://127.0.0.1:5002
```

A demo cria 3 exchanges fictícias (DEMO Alpha, DEMO Beta, DEMO Gamma) com
600 BUY e 300 SELL de 2016 a 2025, com preços BTC/EUR realistas.
O cálculo FIFO produz um mix de mais-valias a longo prazo (isentas)
e a curto prazo (tributáveis).

> A base de dados demo (`data/DEMO_crypto_fifo.db`) é completamente separada
> da base de dados de produção (`data/crypto_fifo.db`). Pode usar ambas.

## Configuração de produção

```bash
# 1. Clonar e configurar
git clone https://github.com/YOUR_USERNAME/crypto-fifo-tracker.git
cd crypto-fifo-tracker
chmod +x setup.sh
./setup.sh              # cria venv, instala pacotes, inicializa BD

# 2. Ativar o ambiente virtual
source venv/bin/activate

# 3. Lançar a interface web
python3 web/app.py
# Abrir http://127.0.0.1:5002
```

A interface web guia através de todo o fluxo de trabalho:

1. **Collect** — carregar os ficheiros CSV das exchanges
2. **Import** — importar cada ficheiro para a base de dados
3. **Status** — verificar consistência CSV ↔ BD
4. **FIFO** — calcular lotes FIFO e mais/menos-valias
5. **Reports** — gerar relatórios IRS em Excel, executar consultas SQL

### Fluxo via linha de comandos (alternativa)

Também se pode executar cada passo pelo terminal:

```bash
# Importar (uma exchange de cada vez)
python3 importers/import_binance_with_fees.py data/binance.csv
python3 importers/verify_exchange_import.py "Binance"

# Calcular FIFO
python3 calculators/calculate_fifo.py

# Gerar relatório IRS
python3 calculators/generate_irs_report.py 2025
```

## Estrutura do projeto

```
crypto-fifo-tracker/
├── config.py                   ← Configuração de país/regras fiscais
├── setup.sh                    ← Configuração automática para produção
├── generate_demo_data.py       ← Gerar ficheiros CSV demo (900 transações)
├── setup_demo.py               ← Construir base de dados demo
├── web/
│   ├── app.py                  ← Aplicação web Flask
│   └── templates/              ← Templates HTML (base, collect, import, status, fifo, reports, manual)
├── calculators/
│   ├── crypto_fifo_tracker.py  ← Biblioteca FIFO principal
│   ├── calculate_fifo.py       ← Script de cálculo FIFO
│   ├── generate_irs_report.py  ← Gerador de relatório IRS (Excel)
│   └── *.sql                   ← Consultas SQL (executáveis pela página Reports)
├── importers/
│   ├── ecb_rates.py            ← Conversão USD→EUR (taxas BCE)
│   ├── crypto_prices.py        ← Preços crypto (CryptoCompare)
│   ├── import_standard_csv.py  ← Importador genérico CSV
│   ├── import_binance_with_fees.py
│   └── ...                     ← Um script por exchange
├── data/
│   ├── crypto_fifo.db          ← Base de dados de produção (excluída do git)
│   ├── DEMO_crypto_fifo.db     ← Base de dados demo (excluída do git)
│   ├── DEMO_*.csv              ← Ficheiros CSV demo (incluídos no git)
│   ├── eurusd.csv              ← Taxas históricas EUR/USD do BCE
│   ├── crypto_prices.csv       ← Preços diários CryptoCompare
│   └── ...                     ← Os seus ficheiros CSV (excluídos do git)
├── doc/
│   ├── schema.sql              ← DDL da base de dados
│   ├── en/                     ← Documentação em inglês
│   └── pt/                     ← Documentação em português
└── tests/                      ← Scripts de teste
```

## Como funciona

### Estratégia de importação: DELETE + reimportar

Cada importador lê um ficheiro CSV completo de uma exchange e substitui todos os dados dessa exchange na base de dados. Sem risco de duplicados. Um ficheiro CSV por exchange com o histórico completo de transações.

### Cálculo FIFO

O `calculate_fifo.py` processa todas as transações desde a primeira até à última, associando cada VENDA à compra mais antiga disponível (FIFO). Recalcula sempre o histórico completo — não é possível calcular apenas um ano, porque os lotes FIFO dependem da cadeia completa.

### Classificação fiscal (Portugal)

| Período de detenção | Classificação | Taxa | Formulário |
|---------------------|--------------|------|------------|
| ≥ 365 dias | Isento | 0% | Anexo G1 Quadro 07 |
| < 365 dias | Tributável | 28% taxa fixa | Anexo J Quadro 9.4A |

As mais-valias isentas devem ser declaradas na mesma. A não declaração pode levar a que a AT trate os fundos como incrementos patrimoniais não justificados.

### Tratamento de comissões

- `total_value` = valor bruto (ANTES de comissões)
- `fee_amount` = comissão armazenada separadamente
- COMPRA: custo de aquisição = total_value + fee_amount
- VENDA: receita = total_value − fee_amount

## Adaptar a outro país

Editar `config.py` para adicionar as regras fiscais do seu país:

```python
COUNTRY = "DE"  # Mudar o país ativo

COUNTRY_PROFILES = {
    "DE": {
        "name": "Deutschland",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
        "tax_rules": {
            "exempt_holding_days": 365,
            "short_term_rate": None,     # Tributado à taxa de rendimento pessoal
            "declare_exempt": True,
        },
        ...
    },
}
```

Consulte `CONTRIBUTING.md` para detalhes sobre como adicionar uma nova exchange ou país.

## Documentação

| Documento | Descrição |
|-----------|-----------|
| [Início rápido](quickstart.md) | Fluxo de trabalho passo a passo |
| [Guia completo](crypto_fifo_guide.md) | Importação, FIFO, relatórios, consultas de verificação |
| [Cripto-para-cripto](crypto_to_crypto_guide.md) | Swaps (BTC→ETH, stablecoins) |
| [Trilha de auditoria](audit_trail_guide.md) | Conformidade, cópias de segurança, retenção de dados |
| [Configuração macOS](macos_sqlite_setup_guide.md) | Configuração de raiz no macOS |
| [Download CSV exchanges](howto_obtain_logs.md) | Como descarregar dados de cada exchange |
| [Recursos fiscais PT](recursos_fiscais.md) | Legislação, guias da AT, prazos |

## Requisitos

- Python 3.11+
- Sem servidor de base de dados (SQLite está incluído no Python)
- Pacotes: `flask`, `pandas`, `pytz`, `openpyxl`, `requests`

## Licença

[MIT](../../LICENSE) — usar, modificar, partilhar livremente.

## Contribuir

Consulte [CONTRIBUTING.md](../../CONTRIBUTING.md) para orientações sobre como adicionar exchanges, países ou melhorias.
