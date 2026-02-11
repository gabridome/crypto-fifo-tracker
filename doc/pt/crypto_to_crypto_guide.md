# Guia — Transações Cripto-para-Cripto

Como tratar swaps BTC→ETH, conversões de stablecoins e negociações entre diferentes criptoativos.

---

## Regra fiscal fundamental

**Cada swap cripto-para-cripto é um evento fiscal** na maioria das jurisdições.

**Nota importante sobre Portugal**: existe divergência de interpretação. Algumas fontes (incluindo a Lei n.º 24-D/2022 e interpretações de fiscalistas) indicam que permutas entre criptoativos **não são tributáveis** no momento da operação — o novo ativo herda o valor de aquisição do anterior, e a tributação ocorre apenas na conversão para moeda fiduciária (fiat). Outras interpretações, mais conservadoras, consideram cada swap um evento tributável.

**Recomendação**: independentemente da interpretação adotada, **registar sempre ambas as operações** (venda + compra) com o valor em EUR. Isto permite:
- Aplicar qualquer das interpretações no momento da declaração
- Ter a cadeia de prova completa para a AT

Quando se troca BTC por ETH:
1. Está a **vender** BTC ao seu valor de mercado em EUR naquele momento
2. Está a **comprar** ETH pelo mesmo valor em EUR
3. Deve calcular **mais/menos-valia** sobre o BTC vendido (via FIFO)
4. O ETH tem um **novo custo de aquisição** igual ao seu valor em EUR no momento da compra

---

## Como funciona no sistema

Um swap cripto-para-cripto é decomposto em duas transações na tabela `transactions`:

| Operação | transaction_type | Exemplo |
|----------|-----------------|---------|
| Venda do cripto cedido | SELL | SELL X BTC a €YY por unidade |
| Compra do cripto recebido | BUY | BUY X ETH a €YY por unidade |

Ambas as transações têm a mesma `transaction_date` e valores EUR equivalentes (menos comissões).

---

## Determinar o valor em EUR

Para o swap, precisa do preço em EUR de ambos os criptoativos no momento da negociação.

**Fontes de preço (por ordem de preferência):**
1. Se a exchange fornece o valor em EUR: usar diretamente
2. Se a exchange fornece o valor em USD: converter via `ecb_rates.py` com a taxa BCE desse dia
3. Se não disponível: usar uma API de preços históricos

**Nota sobre CoinGecko**: o plano gratuito (Demo) só fornece até 365 dias de dados históricos. Para transações mais antigas, usar o CryptoCompare — oferece histórico diário completo desde a origem da moeda no plano gratuito.

```python
# Opção 1: CryptoCompare (recomendado — histórico completo, plano gratuito)
# Requer chave API gratuita de https://www.cryptocompare.com/cryptopian/api-keys
import requests, time

CRYPTOCOMPARE_API_KEY = "YOUR_KEY"

def get_price_cryptocompare(coin, currency, date):
    """Obter preço histórico diário. date é um objeto datetime."""
    timestamp = int(date.timestamp())
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": coin, "tsym": currency, "limit": 1, "toTs": timestamp}
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    response = requests.get(url, headers=headers, params=params)
    data = response.json()["Data"]["Data"]
    return data[-1]["close"]  # preço de fecho desse dia

# Uso:
from datetime import datetime
preco_eur = get_price_cryptocompare("BTC", "EUR", datetime(2014, 5, 21))
print(f"Preço BTC: €{preco_eur}")
time.sleep(0.5)  # respeitar limites de taxa


# Opção 2: CoinGecko (apenas para datas nos últimos 365 dias)
import requests

date_str = "15-03-2025"  # formato dd-mm-yyyy para CoinGecko
url = f"https://api.coingecko.com/api/v3/coins/bitcoin/history?date={date_str}"
response = requests.get(url)
preco_eur = response.json()['market_data']['current_price']['eur']
```

---

## Importação manual

Para um pequeno número de transações cripto-para-cripto, criar um CSV standard e usar `import_standard_csv.py`:

### Formato CSV

```csv
transaction_date,transaction_type,cryptocurrency,amount,price_per_unit,total_value,fee_amount,fee_currency,currency,exchange_name,transaction_id,notes
2024-03-15T14:30:00+00:00,SELL,BTC,0.5,60000,30000,15,EUR,EUR,Binance,c2c_001_sell,Swap BTC→ETH
2024-03-15T14:30:00+00:00,BUY,ETH,8.0,3750,30000,15,EUR,EUR,Binance,c2c_001_buy,Swap BTC→ETH
```

**Regras:**
- Mesma data para ambas as transações
- `total_value` igual (valor EUR do swap, antes de comissões)
- As comissões podem ser divididas entre ambas as transações ou colocadas apenas numa
- Usar `notes` para ligar as duas transações

### Importar

```bash
python3 importers/import_standard_csv.py data/crypto_to_crypto.csv "Binance"
```

---

## Cenários comuns

### Swap em exchange centralizada (Binance Convert, Coinbase swap)

A Binance e a Coinbase têm operações de "Convert" ou swap direto. Verificar o CSV da exchange:
- Binance: procurar "Convert", "Swap Farming", "Small assets exchange BNB"
- Coinbase: procurar "Convert" no tipo de transação

Estas devem ser decompostas em SELL + BUY conforme descrito acima.

### Conversão de stablecoins (BTC→USDT, ETH→USDC)

Mesma lógica: converter para uma stablecoin é uma venda do criptoativo original. O USDT/USDC recebido tem custo de aquisição = valor EUR no momento da conversão.

### Conversão de restos (pequenos saldos → BNB)

A Binance permite converter pequenos saldos residuais para BNB. Tecnicamente tributável, mas tipicamente valores de minimis. Mesmo assim vale a pena registar para completude.

### Tokens wrapped (ETH→WETH)

ETH→WETH pode não ser um evento tributável (mesmo ativo subjacente). Consultar o seu consultor fiscal e documentar a decisão.

---

## Impacto no FIFO

Após importar transações cripto-para-cripto, o FIFO processa-as automaticamente:

1. A **VENDA** de BTC consome lotes FIFO (a partir do mais antigo)
2. A **COMPRA** de ETH cria um novo lote FIFO
3. A mais/menos-valia da VENDA é calculada normalmente
4. O período de detenção é o do lote BTC consumido, não a duração do swap

Se o BTC vendido no swap provém de uma compra com mais de 365 dias, a mais-valia é **isenta** em Portugal — mesmo que convertido imediatamente em ETH.

---

## Cálculo e relatórios

Após importar transações cripto-para-cripto:

```bash
# Recalcular FIFO para todas as criptos
python3 calculators/calculate_fifo.py

# Gerar relatório — inclui todas as criptos com vendas no ano
python3 calculators/generate_irs_report.py YYYY
```

O relatório IRS inclui automaticamente todas as criptomoedas (BTC, ETH, USDC, etc.) com operações de venda no ano.

---

## Verificação

### Verificar transações cripto-para-cripto importadas

```sql
SELECT 
    transaction_date,
    transaction_type,
    cryptocurrency,
    amount,
    price_per_unit,
    total_value,
    exchange_name,
    notes
FROM transactions
WHERE notes LIKE '%Swap%' OR notes LIKE '%Convert%' OR notes LIKE '%c2c%'
ORDER BY transaction_date;
```

### Verificar a mais/menos-valia resultante

```sql
SELECT 
    slm.cryptocurrency,
    COUNT(*) as operacoes,
    SUM(slm.gain_loss) as total_mais_menos_valias,
    SUM(CASE WHEN slm.holding_period_days >= 365 THEN slm.gain_loss ELSE 0 END) as isento,
    SUM(CASE WHEN slm.holding_period_days < 365 THEN slm.gain_loss ELSE 0 END) as tributavel
FROM sale_lot_matches slm
WHERE strftime('%Y', slm.sale_date) = ?
GROUP BY slm.cryptocurrency;
```

---

## Especificidades fiscais portuguesas

- **Cada swap cripto-para-cripto deve ser registado** — independentemente da interpretação fiscal adotada
- **O período de detenção é calculado sobre o ativo vendido** — não sobre o recebido
- **A isenção ≥365 dias aplica-se** mesmo que os ganhos vão para outro criptoativo
- **FIFO é obrigatório** — o lote mais antigo é consumido primeiro
- **Valores em EUR** — usar a taxa BCE do dia para conversões USD
- **Stablecoins** (USDT, USDC) = criptoativo para todos os efeitos fiscais
