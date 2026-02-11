# Recursos Fiscais — Criptoativos em Portugal

Guia de referência com legislação, orientações oficiais, prazos e recursos úteis para a declaração de mais-valias de criptoativos no IRS português.

> **Aviso**: este documento é meramente informativo e não substitui aconselhamento fiscal profissional. As regras fiscais podem mudar anualmente com o Orçamento de Estado. Verificar sempre as fontes oficiais.

---

## Legislação aplicável

### Código do IRS (CIRS)

- **Artigo 10.º, n.º 1, alínea k)** — Define as mais-valias de criptoativos como facto tributável
- **Artigo 10.º, n.º 17** — Definição de criptoativo: "toda a representação digital de valor ou direitos que possa ser transferida ou armazenada eletronicamente recorrendo à tecnologia de registo distribuído ou outra semelhante"
- **Artigo 10.º, n.º 18** — Exclusão dos NFTs (tokens não fungíveis) da definição de criptoativo
- **Artigo 10.º, n.º 19** — Isenção para criptoativos detidos ≥365 dias
- **Artigo 5.º, n.º 2, alínea u)** — Rendimentos de capitais (staking, lending, etc.)
- **Artigo 31.º, n.º 1** — Coeficientes do regime simplificado (0,15 para operações com criptoativos; 0,95 para mineração)
- **Artigo 72.º** — Taxa liberatória de 28% sobre mais-valias de curto prazo

Acesso: [Código do IRS — dre.pt](https://dre.pt/legislacao-consolidada/-/lc/34437675/view)

### Lei do Orçamento de Estado para 2023

- **Lei n.º 24-D/2022, de 30 de dezembro** — Introduziu o regime de tributação de criptoativos em sede de IRS, em vigor desde 1 de janeiro de 2023

Acesso: [Lei 24-D/2022 — dre.pt](https://dre.pt/dre/detalhe/lei/24-d-2022-205694244)

### Regulamento MiCA

- **Regulamento (UE) 2023/1114** — Markets in Crypto-Assets, plenamente aplicável desde dezembro de 2024
- **Lei n.º 69/2025** — Execução do MiCA em Portugal, designando a CMVM e o Banco de Portugal como autoridades de supervisão

---

## Documentação oficial da AT

### Folheto informativo da AT

A Autoridade Tributária publicou um folheto sobre a tributação de criptoativos:

- **"Criptoativos — Conceito fiscal e tributação"**
- URL: [info.portaldasfinancas.gov.pt — Criptoativos.pdf](https://info.portaldasfinancas.gov.pt/pt/apoio_contribuinte/Folhetos_informativos/Documents/Criptoativos.pdf)

Este documento cobre:
- Definição fiscal de criptoativo
- Categorias de rendimento (G, B, E)
- Método FIFO obrigatório
- Regime de isenção (≥365 dias)
- Obrigações declarativas

### Portal das Finanças

- **Submissão do IRS**: [portaldasfinancas.gov.pt](https://www.portaldasfinancas.gov.pt/)
- **Modelo 3**: declaração anual de IRS
- **Simulador de IRS**: disponível no portal após abertura do período de entrega

---

## Anexos do IRS — Onde declarar

### Investidor particular (Categoria G — Mais-valias)

| Situação | Anexo | Quadro | Notas |
|----------|-------|--------|-------|
| Vendas com detenção <365 dias, exchange **em Portugal** | Anexo G | Quadro 18A | Raro — a maioria das exchanges está no estrangeiro |
| Vendas com detenção <365 dias, exchange **no estrangeiro** | Anexo J | Quadro 9.4A | Caso mais comum |
| Vendas com detenção ≥365 dias (isentas) | Anexo G1 | Quadro 07 | Obrigatório declarar mesmo sendo isento |
| Perda de residência fiscal (exit tax) | Anexo G | Quadro 18B | Tributação sobre valores latentes |

### Atividade profissional (Categoria B)

| Situação | Anexo | Quadro | Notas |
|----------|-------|--------|-------|
| Operações com criptoativos (trading) | Anexo B | Quadro 4A, campo 419 | Coeficiente 0,15 no regime simplificado |
| Mineração de criptoativos | Anexo B | Quadro 4A, campo 422 | Coeficiente 0,95 no regime simplificado |

### Rendimentos de capitais (Categoria E)

| Situação | Anexo | Quadro | Notas |
|----------|-------|--------|-------|
| Staking, lending, airdrops (pagos em EUR) | Anexo E | Quadro 4A, código E21 | Taxa 28% |
| Staking, lending (pagos em cripto) | — | — | Tributados como mais-valia quando vendidos |

---

## Regras fundamentais

### Método FIFO

O método FIFO (First In, First Out) é **obrigatório** pela legislação portuguesa. Para cada venda, consome-se o lote de compra mais antigo. Deve ser aplicado por criptoativo (não por exchange).

### Isenção ≥365 dias

Mais-valias de criptoativos detidos por **365 dias ou mais** estão **isentas** de IRS, desde que:
- A contraparte não resida em jurisdição de regime fiscal privilegiado ("lista negra")
- A operação ocorra na UE, EEE ou país com convenção de troca de informações fiscais

**Atenção**: a isenção **não dispensa a obrigação de declaração**. As operações isentas devem ser reportadas no Anexo G1.

### Valores em EUR

Todos os valores devem ser declarados em EUR. Para operações em USD ou outra moeda, usar a taxa de câmbio oficial do BCE (Banco Central Europeu) para o dia da transação.

### Agregação diária

A AT exige agregação diária: múltiplas operações no mesmo dia, na mesma exchange, com o mesmo tipo (isento/tributável), são agregadas numa única linha da declaração.

---

## Prazos

| Evento | Prazo | Notas |
|--------|-------|-------|
| Entrega do IRS (Modelo 3) | 1 de abril a 30 de junho | Ano seguinte ao dos rendimentos |
| Pagamento do IRS | Agosto/setembro | Data indicada na nota de liquidação |
| Retenção de documentação | 7 anos | Após a data de entrega da declaração |

---

## Permutas cripto-para-cripto

Existe divergência interpretativa sobre a tributação de permutas entre criptoativos:

**Interpretação 1 (mais favorável)**: Permutas cripto-para-cripto **não são tributáveis**. O novo ativo herda o valor de aquisição do anterior. A tributação ocorre apenas na conversão para moeda fiduciária.

**Interpretação 2 (mais conservadora)**: Cada permuta é um evento tributável, com mais-valia calculada sobre o ativo cedido.

**Recomendação**: registar sempre ambas as operações para poder aplicar qualquer interpretação. Consultar um profissional fiscal.

---

## Coimas e penalidades

A não declaração ou declaração incorreta de rendimentos de criptoativos pode resultar em:

- **Coima por falta de declaração**: €150 a €3.750
- **Coima por declaração incorreta**: valores variáveis
- **Fraude fiscal**: em casos graves, com consequências penais
- **Incrementos patrimoniais não justificados**: se a AT detetar movimentos bancários sem declaração correspondente, pode tributar como rendimento não declarado

---

## Recursos úteis

### Fontes oficiais

| Recurso | URL |
|---------|-----|
| Portal das Finanças | https://www.portaldasfinancas.gov.pt/ |
| Folheto AT — Criptoativos | https://info.portaldasfinancas.gov.pt/pt/apoio_contribuinte/Folhetos_informativos/Documents/Criptoativos.pdf |
| Código do IRS | https://dre.pt/legislacao-consolidada/-/lc/34437675/view |
| Lei 24-D/2022 (OE 2023) | https://dre.pt/dre/detalhe/lei/24-d-2022-205694244 |

### Ordens profissionais

| Recurso | URL |
|---------|-----|
| Ordem dos Contabilistas Certificados (OCC) | https://www.occ.pt/ |
| Artigo OCC sobre criptoativos | https://portal.occ.pt/pt-pt/noticias/irs-criptoativos-0 |
| APECA — Enquadramento fiscal | https://www.apeca.pt/ |

### Taxas de câmbio

| Recurso | URL |
|---------|-----|
| Taxas BCE (EUR/USD) | https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html |
| Download CSV taxas BCE | https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip |

### APIs para preços históricos

| Recurso | Notas |
|---------|-------|
| CryptoCompare | Plano gratuito com histórico completo. https://www.cryptocompare.com/ |
| CoinGecko | Plano gratuito limitado a 365 dias. https://www.coingecko.com/en/api |

---

## Atualizações legislativas

A tributação de criptoativos em Portugal está em evolução. Recomenda-se verificar anualmente:

1. **Orçamento de Estado** — publicado em dezembro, pode alterar taxas, isenções e regras
2. **Circulares e informações vinculativas da AT** — interpretações oficiais publicadas ao longo do ano
3. **Regulamento MiCA** — novas obrigações de reporte para exchanges e prestadores de serviços
4. **Diretiva DAC8** — troca automática de informações sobre criptoativos entre estados-membros da UE (a partir de 2026)

---

## Disclaimer

Este documento é fornecido apenas para fins informativos. Não constitui aconselhamento fiscal, jurídico ou financeiro. Consulte sempre um contabilista certificado ou consultor fiscal para a sua situação específica. As leis e regulamentos podem mudar — verifique sempre as fontes oficiais antes de tomar decisões fiscais.
