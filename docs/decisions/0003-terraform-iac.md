---
title: Terraform as the single IaC tool
type: decision
status: draft
owner: gymbooking
last-verified: 2026-11-19
relevant-code:
  - infra/terraform/
  - tests/test_terraform.py
---

# Terraform as the single IaC tool

## Contesto

L'ambiente era diviso tra lo stack SAM `gymbot-telegram` (`bot-template.yaml`) e risorse create a mano con la
CLI (Lambda legacy, ruoli, schedule, topic SNS). Non c'era un modo unico per ricrearlo o per vedere la deriva.

## Decisione

Terraform gestisce tutto, con import delle risorse esistenti e poi dismissione dello stack SAM con
`DeletionPolicy: Retain`. State in S3 con lockfile nativo (niente DynamoDB). I parametri SSM `/gymbot/*` restano
fuori (ADR 0001). Stato e espressione della schedule sono ignorati da Terraform (ADR 0002). Il codice delle Lambda non
viene distribuito da Terraform finche' non si rimuove `ignore_changes` in un commit dedicato.

## Conseguenze

- Un solo posto per risorse, IAM e schedule; `plan` mostra la deriva.
- Lo state contiene le variabili d'ambiente della Lambda legacy (segreti in chiaro): bucket cifrato e privato.
- Stato: `draft`. Import eseguito il 2026-11-19 (31 risorse); resta da dismettere lo stack SAM (vedi `docs/how-to/terraform.md`).
