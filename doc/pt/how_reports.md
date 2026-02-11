# Como Descarregar Dados das Exchanges

## Coinbase Prime

Após login, ir a **Activity → Orders → Download CSV**.

O relatório contém colunas: order_id, status, product, side, type, limit_price, average_filled_price, etc.
Valores em USD — o importador `import_coinbase_prime.py` converte para EUR via `ecb_rates.py`.

## Binance

Ir a **As minhas Ordens → Histórico de Negociação** → ícone de exportação (segunda linha).

URL direto: `https://www.binance.com/en/my/orders/exchange/usertrade`

Formato:
```csv
"Date(UTC)","Pair","Side","Price","Executed","Amount","Fee"
"YYYY-MM-DD HH:MM:SS","BTCEUR","BUY","XXXXX.XX","X.XXXXXBTC","XXXX.XXXXXXXEUR","X.XXXXXXXXXBTC"
```

As quantidades incluem um sufixo de moeda (ex: "0.02776BTC", "1731.98EUR"). O importador remove-os automaticamente.

## Coinbase

**Definições → Atividade → Gerar Relatório**. Selecionar formato CSV.

Formato:
```csv
Timestamp,Transaction Type,Asset,Quantity Transacted,EUR Spot Price at Transaction,EUR Subtotal,EUR Total (inclusive of fees and/or spread),EUR Fees and/or Spread,Notes
```

## Bitstamp

**Histórico de Transações → Exportar**. Valores em USD — convertidos para EUR durante a importação.

## Kraken

**Histórico → Exportar → Ledgers**. Formato com colunas: txid, refid, time, type, subtype, aclass, asset, amount, fee, balance.

## Bitfinex

**Relatórios → Histórico de Negociação → Exportar CSV**. Valores em USD — convertidos para EUR durante a importação.

## Wirex

**Histórico de Transações → Descarregar**. Nota: o ficheiro pode estar em codificação UTF-16. Converter primeiro:
```bash
iconv -f UTF-16LE -t UTF-8 wirex.csv > wirex_utf8.csv
```

## Revolut

**Cripto → Extrato → Descarregar CSV**.

## TRT (TheRockTrading)

Usar o ficheiro CSV arquivado. Formato proprietário com possíveis registos multilinha.

## Mt.Gox

Usar o ficheiro CSV arquivado. Os preços podem estar em USD ou JPY — o importador trata a conversão.

## Transações manuais (OTC, herança, etc.)

Criar um CSV com o formato standard:
```csv
transaction_date,transaction_type,cryptocurrency,amount,price_per_unit,total_value,fee_amount,fee_currency,currency,exchange_name,transaction_id,notes
YYYY-MM-DDTHH:MM:SS+00:00,BUY,BTC,X.XX,XXXXX.XX,XXXXX.XX,XX.XX,EUR,EUR,OTC,manual_001,Descrição
```

Importar com:
```bash
python3 importers/import_standard_csv.py data/ficheiro.csv "Nome Exchange"
```
