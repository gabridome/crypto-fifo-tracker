# Crypto FIFO Tracker

Sistema open-source de rastreamento FIFO para mais-valias de criptoativos, concebido para a declaração de IRS em Portugal. Adaptável a outras jurisdições.

**[English documentation](../en/README.md)**

## Funcionalidades

- **Base de dados SQLite** — ficheiro único, sem servidor, portátil
- **Método FIFO** — obrigatório pela legislação fiscal portuguesa
- **Multi-exchange** — importadores dedicados para Binance, Coinbase, Coinbase Prime, Bitstamp, Bitfinex, Kraken, Mt.Gox, TRT, Wirex, Revolut e importador genérico CSV
- **Moeda EUR** — conversão USD→EUR via taxas históricas oficiais do BCE
- **Relatórios IRS** — Excel com Anexo G1 (isentos, ≥365 dias) e Anexo J (tributáveis, <365 dias)
- **Agregação diária** — conforme exigido pela Autoridade Tributária
- **Preparado para multi-país** — regras fiscais separadas em `config.py`

## Início rápido

```bash
# 1. Clonar e configurar
git clone https://github.com/YOUR_USERNAME/crypto-fifo-tracker.git
cd crypto-fifo-tracker
chmod +x setup.sh
./setup.sh

# 2. Ativar o ambiente virtual
source venv/bin/activate

# 3. Colocar os ficheiros CSV das exchanges em data/

# 4. Importar (uma exchange de cada vez)
python3 importers/import_binance_with_fees.py
python3 importers/verify_exchange_import.py "Binance"

# 5. Calcular FIFO (~2 min para conjuntos grandes)
python3 calculators/calculate_fifo.py

# 6. Gerar relatório IRS
python3 calculators/generate_irs_report.py 2025
```

## Estrutura do projeto

```
crypto-fifo-tracker/
├── config.py                   ← Configuração de país/regras fiscais
├── setup.sh                    ← Script de configuração automática
├── calculators/
│   ├── crypto_fifo_tracker.py  ← Biblioteca FIFO principal
│   ├── calculate_fifo.py       ← Script de cálculo FIFO
│   └── generate_irs_report.py  ← Gerador de relatório IRS (Excel)
├── importers/
│   ├── ecb_rates.py            ← Conversão USD→EUR (taxas BCE)
│   ├── import_binance_with_fees.py
│   ├── import_coinbase_prime.py
│   ├── import_standard_csv.py  ← Importador genérico CSV
│   ├── verify_exchange_import.py
│   └── ...                     ← Um script por exchange
├── data/                       ← TODOS os dados pessoais (excluídos do git exceto amostras)
│   ├── crypto_fifo.db          ← Base de dados SQLite (criada pelo setup.sh)
│   ├── eurusd.csv              ← Taxas históricas EUR/USD do BCE
│   ├── sample_transactions.csv ← Dados de exemplo (incluídos no git)
│   ├── ...                     ← Os seus ficheiros CSV
│   ├── reports/                ← Relatórios IRS gerados
│   ├── backups/                ← Cópias de segurança da BD
│   └── supporting_documents/   ← Extratos, faturas
├── doc/
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
| [Download CSV exchanges](how_reports.md) | Como descarregar dados de cada exchange |
| [Recursos fiscais PT](recursos_fiscais.md) | Legislação, guias da AT, prazos |

## Requisitos

- Python 3.11+
- Sem servidor de base de dados (SQLite está incluído no Python)
- Pacotes: `pandas`, `pytz`, `openpyxl`, `requests`

## Licença

[MIT](../../LICENSE) — usar, modificar, partilhar livremente.

## Contribuir

Consulte [CONTRIBUTING.md](../../CONTRIBUTING.md) para orientações sobre como adicionar exchanges, países ou melhorias.
