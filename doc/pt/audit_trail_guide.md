# Conformidade e Trilha de Auditoria

Documentação para rastreabilidade fiscal e defesa em caso de inspeção tributária.

---

## Princípios

O sistema fornece evidências verificáveis de:
- Origem de cada transação (exchange, ficheiro CSV, data de importação)
- Metodologia FIFO com cadeia de prova compra→venda
- Período de detenção exato para cada associação
- Conversão USD→EUR via taxas oficiais do BCE
- Integridade dos dados via hashes e cópias de segurança

---

## O que guardar para cada ano fiscal

### 1. Base de dados e relatórios

```
data/backups/crypto_fifo.db.backup_YYYYMMDD   ← Snapshot da BD após FIFO
data/reports/IRS_Crypto_FIFO_YYYY.xlsx        ← Relatório IRS com Anexo G1 e Anexo J
```

### 2. Ficheiros CSV fonte

Todos os ficheiros CSV originais descarregados das exchanges, mantidos em `data/`. Nunca modificar ficheiros originais — se for necessária limpeza, criar uma cópia.

### 3. Taxas de câmbio

O ficheiro `data/eurusd.csv` com as taxas históricas do BCE usadas para conversões USD→EUR. Esta é a referência oficial do BCE.

### 4. Documentos de suporte

```
data/supporting_documents/
├── exchange_statements/    ← Extratos de conta das exchanges
├── price_evidence/         ← Screenshots/JSON para preços reconstruídos
└── other/                  ← Faturas, contratos para transações OTC
```

---

## Verificação da integridade dos dados

### Verificação de consistência da importação

Após cada importação, verificar:

```bash
python3 importers/verify_exchange_import.py "Nome da Exchange"
```

O script verifica:
- Número de transações BUY e SELL
- Quantidade total de BTC
- Total de comissões
- Consistência de preços

### Verificação de consistência FIFO

```sql
-- Todas as vendas devem estar associadas
SELECT COUNT(*) as vendas_nao_associadas
FROM transactions t
LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
WHERE t.transaction_type = 'SELL'
AND t.cryptocurrency = 'BTC'
AND slm.id IS NULL;
-- Resultado esperado: 0

-- Sem montantes negativos
SELECT COUNT(*) FROM transactions WHERE amount < 0;
-- Resultado esperado: 0

-- Todas as transações têm datas válidas
SELECT COUNT(*) FROM transactions WHERE transaction_date IS NULL OR transaction_date = '';
-- Resultado esperado: 0
```

### Verificação de reprodutibilidade

O cálculo FIFO é determinístico: com os mesmos dados em `transactions`, o `calculate_fifo.py` produz sempre os mesmos resultados em `sale_lot_matches`. Para verificar:

```bash
# Backup pré-cálculo
cp data/crypto_fifo.db data/backups/crypto_fifo.db.pre_fifo

# Calcular FIFO
python3 calculators/calculate_fifo.py

# Snapshot dos resultados
sqlite3 data/crypto_fifo.db "SELECT SUM(gain_loss), COUNT(*) FROM sale_lot_matches" > checksum_a.txt

# Recalcular FIFO
python3 calculators/calculate_fifo.py

# Comparar
sqlite3 data/crypto_fifo.db "SELECT SUM(gain_loss), COUNT(*) FROM sale_lot_matches" > checksum_b.txt
diff checksum_a.txt checksum_b.txt
# Resultado esperado: sem diferenças
```

---

## Rastreabilidade de alterações

### Backup antes de cada operação crítica

```bash
# Antes de importar novos dados
cp data/crypto_fifo.db data/backups/crypto_fifo.db.backup_before_import_$(date +%Y%m%d)

# Antes de recalcular FIFO
cp data/crypto_fifo.db data/backups/crypto_fifo.db.backup_before_fifo_$(date +%Y%m%d)
```

### Hash da base de dados

```bash
# Calcular SHA-256 da BD após gerar o relatório final
shasum -a 256 data/crypto_fifo.db > data/backups/crypto_fifo.db.sha256
```

### Hashes dos ficheiros CSV fonte

```bash
# Criar hashes para todos os CSVs
shasum -a 256 data/*.csv > data/backups/csv_hashes.sha256
```

---

## Consultas de auditoria

### Transações por exchange e tipo

```sql
SELECT 
    exchange_name,
    transaction_type,
    cryptocurrency,
    COUNT(*) as n,
    SUM(amount) as quantidade,
    SUM(total_value) as valor_eur,
    SUM(fee_amount) as comissoes
FROM transactions
GROUP BY exchange_name, transaction_type, cryptocurrency
ORDER BY exchange_name, transaction_type;
```

### Resumo anual por classificação fiscal

```sql
SELECT 
    strftime('%Y', sale_date) as ano,
    cryptocurrency,
    COUNT(*) as operacoes,
    SUM(amount_sold) as quantidade_vendida,
    SUM(gain_loss) as total_mais_menos_valias,
    SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as isento,
    SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END) as tributavel
FROM sale_lot_matches
GROUP BY ano, cryptocurrency
ORDER BY ano, cryptocurrency;
```

### Origem das vendas (em que exchange e ano foi a cripto originalmente comprada)

```sql
SELECT 
    t_purchase.exchange_name as exchange_compra,
    strftime('%Y', slm.purchase_date) as ano_compra,
    COUNT(*) as associacoes,
    SUM(slm.amount_sold) as quantidade,
    SUM(slm.gain_loss) as mais_menos_valia
FROM sale_lot_matches slm
JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
WHERE strftime('%Y', slm.sale_date) = ?
GROUP BY exchange_compra, ano_compra
ORDER BY ano_compra, exchange_compra;
```

### Verificar taxas BCE utilizadas

Para transações em USD, as taxas BCE são incorporadas no momento da importação. Podem ser verificadas comparando o `price_per_unit` (em EUR) com o preço original em USD no CSV e a taxa BCE para esse dia.

---

## O que apresentar às autoridades fiscais

### Documentação básica

1. **Relatório IRS** (`IRS_Crypto_FIFO_YYYY.xlsx`) — Folhas Resumo + Anexo G1 + Anexo J
2. **Resumo anual** — saída do `generate_annual_summary.py`
3. **Ficheiros CSV originais** das exchanges
4. **Taxas BCE** (`data/eurusd.csv`)

### Em caso de inspeção

Documentação adicional:
1. **Cadeia de prova FIFO** — consulta mostrando cada venda associada à sua compra original
2. **Extratos de conta** das exchanges
3. **Hash da base de dados** à data da declaração
4. **Esquema da base de dados** (`doc/schema.sql`)
5. **Mapeamento de colunas** (`doc/IMPORT_MAPPING.md`)

### Explicação da metodologia

O método FIFO (First In, First Out) é obrigatório pela legislação fiscal portuguesa. Para cada venda de criptoativo, o sistema identifica automaticamente a compra mais antiga disponível e calcula:

- **Período de detenção** = data da venda − data da compra (em dias)
- **Custo de aquisição** = preço original de compra + comissões
- **Receita** = preço de venda − comissões
- **Mais/menos-valia** = receita − custo de aquisição
- **Classificação** = isento se detenção ≥365 dias, tributável caso contrário

Todos os valores estão em EUR. Para exchanges que operam em USD, a conversão usa a taxa do BCE (Banco Central Europeu) para a data da transação.

---

## Retenção de dados

Obrigação de retenção: **mínimo 7 anos** a partir da data da declaração.

Guardar:
- [ ] Base de dados `data/crypto_fifo.db` (com backup datado)
- [ ] Ficheiros CSV fonte das exchanges
- [ ] Taxas BCE (`data/eurusd.csv`)
- [ ] Relatórios IRS gerados (`.xlsx`)
- [ ] Hashes SHA-256 da base de dados e CSVs
- [ ] Documentos de suporte (extratos, faturas)

Recomendação: arquivar tudo em pelo menos 2 suportes de armazenamento diferentes.
