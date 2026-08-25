"""Splice new staged execute_strategy + batch APIs into engine.py (Milestone C).

Replaces the monolithic execute_strategy body (between the
"Execute signals from Phase 2 agents" banner and the "Legacy adapter"
banner) with a composition of reusable no-I/O stages shared with
MultiPairRuntime — N=1 parity by construction.
"""

from pathlib import Path

ENGINE = Path("src/trading_agent/execution/engine.py")
START_MARK = (
    "    # ── Execute signals from Phase 2 agents ────────────────────────────\n"
)
END_MARK = "    # ── Legacy adapter: AgentMessage → StrategyRuntime ──────────────\n"

NEW = '''    # ── Milestone C: staged preparation/planning/submission ────────────
    #
    # execute_strategy() is now a THIN COMPOSITION of the same no-I/O stages
    # that MultiPairRuntime uses for batches. Single-pair == batch with N=1.

    def _prepare_from_runtime(
        self,
        strategy_runtime: StrategyRuntime,
        market_data: Any,
        observation: EnrichedMarketObservation | None,
    ) -> PairPreparedDecision:
        """Resolve → StrategyOutput → DecisionAuthority. NO allocation,
        NO exposure validation, NO broker I/O after this point in prepare."""
        symbol = strategy_runtime.symbol
        timeframe = strategy_runtime.timeframe

        def _no_action(status: str, signal: str = "HOLD") -> PairPreparedDecision:
            return PairPreparedDecision(
                symbol=symbol,
                timeframe=timeframe,
                artifact_id=strategy_runtime.artifact_id,
                strategy_name=strategy_runtime.strategy_name,
                observation=observation,
                strategy_output=None,
                risk_decision=None,
                signal=signal,
                prepare_status=status,
            )

        # Execute strategy to get signal
        strategy_output = strategy_runtime.execute(
            market_data=market_data,
            portfolio_state=None,  # Portfolio state injected by authority chain
            observation_id=observation.observation_id if observation else None,
            data_manifest_id=getattr(observation, "data_manifest_id", None),
            feature_artifact_id=getattr(observation, "feature_artifact_id", None),
            research_run_id=getattr(observation, "research_run_id", None),
        )

        signal_value = str(strategy_output.signal).upper()
        if signal_value == "HOLD":
            logger.info(
                f"Strategy {strategy_runtime.strategy_name}: HOLD — no action"
            )
            return _no_action("hold")

        # Sync protective orders with actual positions
        self._sync_protective_orders()

        # Get current price
        price_info = self._get_current_price(symbol)
        if price_info is None:
            logger.warning(f"Cannot execute strategy: no price data for {symbol}")
            return _no_action("no_price")
        current_price, exchange_timestamp = price_info
        if current_price <= 0:
            logger.warning(f"Cannot execute strategy: no price data for {symbol}")
            return _no_action("no_price")

        # Observation: must come from market data layer
        if observation is None:
            logger.warning(
                "execute_strategy requires a market observation from the data layer"
            )
            return _no_action("bad_observation")
        if not observation.is_closed:
            logger.warning(
                f"Refusing to execute from unclosed observation "
                f"{observation.observation_id}"
            )
            return _no_action("bad_observation")

        # ── Build DecisionInput from StrategyOutput ──────────────────
        existing_pos = self.exchange.get_position(symbol)
        current_qty = existing_pos.quantity if existing_pos else 0.0
        current_notional = current_qty * current_price
        equity = self.exchange.get_total_equity()
        current_exposure = current_notional / equity if equity > 0 else 0.0
        available_cash = self.exchange.get_balance("USDT")

        decision_input = DecisionInput(
            strategy_output=strategy_output,  # Direct StrategyOutput path
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            current_exposure=current_exposure,
            equity=equity,
            available_cash=available_cash,
            portfolio_value=equity,
            observation_id=observation.observation_id if observation else None,
            regime=getattr(observation, "regime", None),
            volatility_pct=getattr(observation, "volatility_pct", None),
        )

        decision_output = self.decision_authority.decide(decision_input)
        risk_decision = UnifiedRiskDecision(
            decision_id=decision_output.risk_decision.decision_id,
            forecast_fingerprint=decision_output.risk_decision.forecast_fingerprint,
            model_artifact_id=(
                decision_output.risk_decision.model_artifact_id
                or strategy_runtime.artifact_id
            ),
            requested_target_exposure=(
                decision_output.risk_decision.requested_target_exposure
            ),
            allowed_target_exposure=decision_output.risk_decision.allowed_target_exposure,
            max_new_exposure=decision_output.risk_decision.max_new_exposure,
            reduce_only=decision_output.risk_decision.reduce_only,
            risk_level=decision_output.risk_decision.risk_level,
            reason_codes=decision_output.risk_decision.reason_codes,
            calibration_state=decision_output.risk_decision.calibration_state,
            calibration_artifact_id=(
                decision_output.risk_decision.calibration_artifact_id
            ),
            calibration_ece=decision_output.risk_decision.calibration_ece,
            ood_state=decision_output.risk_decision.ood_state,
            ood_score=decision_output.risk_decision.ood_score,
            regime_state=decision_output.risk_decision.regime_state,
            regime_entropy=decision_output.risk_decision.regime_entropy,
            interval_width=decision_output.risk_decision.interval_width,
            created_at=decision_output.risk_decision.created_at,
            metadata=decision_output.risk_decision.metadata,
            warnings=decision_output.risk_decision.warnings,
            authority_chain=decision_output.causation_chain.links,
        )
        target = TargetExposure(
            target_exposure_pct=decision_output.target_exposure.target_exposure_pct,
            max_new_exposure_pct=(
                decision_output.target_exposure.max_new_exposure_pct
            ),
            reduce_only=decision_output.target_exposure.reduce_only,
            confidence=decision_output.target_exposure.confidence,
            authority_chain=decision_output.causation_chain.links,
        )

        total_portfolio_exposure = (
            sum(
                (pos.quantity * self.exchange._last_price_cache.get(pos.symbol, 0.0))
                / equity
                for pos in self.exchange.get_all_positions()
                if pos.is_active
            )
            if equity > 0
            else 0.0
        )

        return PairPreparedDecision(
            symbol=symbol,
            timeframe=timeframe,
            artifact_id=strategy_runtime.artifact_id,
            strategy_name=strategy_runtime.strategy_name,
            observation=observation,
            strategy_output=strategy_output,
            risk_decision=risk_decision,
            requested_target_exposure=target.target_exposure_pct,
            current_exposure=current_exposure,
            signal=signal_value,
            causation_chain=decision_output.causation_chain,
            current_price=current_price,
            equity=equity,
            available_cash=available_cash,
            current_quantity=current_qty,
            total_portfolio_exposure=total_portfolio_exposure,
            prepare_status="ok",
        )

    def prepare_promoted_strategy(
        self,
        symbol: str,
        timeframe: str,
        environment: str | Environment,
        market_data_input: MarketDataInput,
    ) -> PairPreparedDecision | None:
        """No-I/O preparation of ONE promoted binding (batch stage).

        Resolves the promoted artifact, runs the strategy, seeds the price,
        and runs DecisionAuthority. Performs NO allocation, NO exposure
        validation, NO order planning, NO broker I/O.

        Returns None when no eligible promotion record exists (fail-closed
        upstream: required-universe policy treats None as a failed binding).
        """
        if self.resolver is None:
            raise RuntimeError(
                "prepare_promoted_strategy requires RuntimeStrategyResolver "
                "(promotion_store + artifact_store at engine construction)"
            )
        env = (
            Environment(environment.lower())
            if isinstance(environment, str)
            else environment
        )
        strategy_runtime = self.resolver.resolve_for(symbol, timeframe, env)
        if strategy_runtime is None:
            logger.warning(
                f"No promoted strategy resolved for {symbol} {timeframe} "
                f"{env.value}"
            )
            return None

        observation = self.build_observation(market_data_input)
        # Seed fresh price so DecisionAuthority sees a tradable quote
        try:
            close_price = float(
                market_data_input.data.tail(1).to_dicts()[0]["close"]
            )
            self.update_prices({symbol: close_price})
        except Exception as e:
            logger.warning(
                "prepare_promoted_strategy[%s]: cannot seed price: %s", symbol, e
            )

        return self._prepare_from_runtime(
            strategy_runtime, market_data_input.data, observation
        )

    def build_observation(self, market_data_input: MarketDataInput) -> Any:
        """EnrichedMarketObservation from the LAST CLOSED bar, carrying REAL
        provenance from the MarketDataInput (never fabricated IDs)."""
        tail = market_data_input.data.tail(1).to_dicts()[0]
        observed_at = datetime.now(UTC)

        bar_ts = tail.get("timestamp")
        if isinstance(bar_ts, datetime):
            bar_close_at = bar_ts if bar_ts.tzinfo else bar_ts.replace(tzinfo=UTC)
        else:
            bar_close_at = observed_at

        return EnrichedMarketObservation(
            symbol=market_data_input.symbol,
            observed_at=observed_at,
            open=float(tail["open"]),
            high=float(tail["high"]),
            low=float(tail["low"]),
            close=float(tail["close"]),
            volume=float(tail.get("volume", 0.0)),
            features={},
            timeframe=market_data_input.timeframe,
            bar_close_at=bar_close_at,
            is_closed=True,
            data_manifest_id=market_data_input.data_manifest_id,
            feature_artifact_id=market_data_input.feature_artifact_id,
        )

    def build_allocation_request(
        self,
        prepared: PairPreparedDecision,
        snapshot: PortfolioSnapshot | None = None,
    ) -> AllocationRequest:
        """AllocationRequest for one prepared pair against SHARED truth.

        When ``snapshot`` is given (batch mode), equity/cash/exposures come
        from the authoritative shared PortfolioSnapshot; otherwise from the
        values captured at prepare time (single-pair parity path).
        """
        if snapshot is not None:
            equity = float(snapshot.equity)
            available_cash = float(snapshot.available_cash)
            total_portfolio_exposure = float(snapshot.gross_exposure)
            symbol_total: float | None = float(
                snapshot.symbol_exposures.get(prepared.symbol, 0.0)
            )
        else:
            equity = prepared.equity
            available_cash = prepared.available_cash
            total_portfolio_exposure = prepared.total_portfolio_exposure
            symbol_total = None  # falls back to current_exposure (legacy parity)

        return AllocationRequest(
            strategy_id=prepared.artifact_id,
            symbol=prepared.symbol,
            risk_decision=prepared.risk_decision,
            current_exposure=prepared.current_exposure,
            equity=equity,
            available_cash=available_cash,
            portfolio_exposure=total_portfolio_exposure,
            correlation_cluster=None,
            symbol_total_exposure=symbol_total,
            causation_chain=prepared.causation_chain,
        )

    def finalize_prepared_decision(
        self,
        prepared: PairPreparedDecision,
        *,
        approved_target: TargetExposure,
        combined_chain: CausationChain,
    ) -> FinalizedPairDecision | None:
        """ExposureAuthority validation of an ALLOCATED target (no broker I/O).

        ``approved_target`` carries the allocation result (single-pair:
        PortfolioAllocator.allocate; batch: allocate_batch via the target
        vector). Returns None when ExposureAuthority blocks the pair.
        """
        exposure_input = ExposureValidationInput(
            target_exposure=approved_target,
            symbol=prepared.symbol,
            strategy_id=prepared.artifact_id,
            current_exposure=prepared.current_exposure,
            portfolio_exposure=prepared.total_portfolio_exposure,
            strategy_exposure=prepared.current_exposure,
            equity=prepared.equity,
            available_cash=prepared.available_cash,
            correlation_exposure=0.0,
            causation_chain=combined_chain,
        )

        exposure_output = self.exposure_authority.validate(exposure_input)
        if not exposure_output.allowed:
            logger.warning(
                "Order blocked by ExposureAuthority [%s %s]: %s",
                prepared.symbol,
                prepared.timeframe,
                exposure_output.reason,
            )
            return None

        base_rd = prepared.risk_decision
        assert base_rd is not None
        risk_decision = UnifiedRiskDecision(
            decision_id=base_rd.decision_id,
            forecast_fingerprint=base_rd.forecast_fingerprint,
            model_artifact_id=base_rd.model_artifact_id,
            requested_target_exposure=base_rd.requested_target_exposure,
            allowed_target_exposure=exposure_output.allowed_target_exposure,
            max_new_exposure=exposure_output.allowed_max_new_exposure,
            reduce_only=base_rd.reduce_only,
            risk_level=base_rd.risk_level,
            reason_codes=base_rd.reason_codes,
            calibration_state=base_rd.calibration_state,
            calibration_artifact_id=base_rd.calibration_artifact_id,
            calibration_ece=base_rd.calibration_ece,
            ood_state=base_rd.ood_state,
            ood_score=base_rd.ood_score,
            regime_state=base_rd.regime_state,
            regime_entropy=base_rd.regime_entropy,
            interval_width=base_rd.interval_width,
            created_at=base_rd.created_at,
            metadata=base_rd.metadata,
            warnings=base_rd.warnings + exposure_output.warnings,
            authority_chain=exposure_output.causation_chain.links,
        )
        target = TargetExposure(
            target_exposure_pct=exposure_output.allowed_target_exposure,
            max_new_exposure_pct=exposure_output.allowed_max_new_exposure,
            reduce_only=risk_decision.reduce_only,
            confidence=approved_target.confidence,
            authority_chain=exposure_output.causation_chain.links,
        )

        no_change = abs(target.target_exposure_pct - prepared.current_exposure) < 1e-9
        if no_change:
            logger.info(
                "[%s %s] Target exposure equals current (%.4f) — no action",
                prepared.symbol,
                prepared.timeframe,
                prepared.current_exposure,
            )
        return FinalizedPairDecision(
            prepared=prepared,
            approved_target_exposure=target.target_exposure_pct,
            risk_decision=risk_decision,
            target=target,
            causation_chain=exposure_output.causation_chain,
            no_change=no_change,
        )

    def plan_pair_order(self, finalized: FinalizedPairDecision) -> PairOrderPlan:
        """Plan the order for one finalized pair. NO broker I/O."""
        prepared = finalized.prepared
        symbol = prepared.symbol
        timeframe = prepared.timeframe

        if finalized.no_change:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=None,
                idempotency_key=None,
                detail="target equals current exposure",
            )

        assert self.execution_service is not None
        assert self.planner is not None

        pair_rules = self.planner.rules_for(symbol)
        rule_id = getattr(pair_rules, "rule_id", None) or (
            getattr(pair_rules, "name", None)
        )
        if pair_rules is None:
            logger.error(
                "No instrument rules registered for %s — cannot plan", symbol
            )
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.BLOCKED,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=None,
                idempotency_key=None,
                detail="missing instrument rules",
            )

        portfolio = CurrentPortfolioState(
            symbol=symbol,
            current_exposure=prepared.current_exposure,
            equity=prepared.equity,
            existing_quantity=prepared.current_quantity,
            available_cash=prepared.available_cash,
        )
        price = MarketPrice(
            symbol=symbol,
            mid=prepared.current_price,
            bid=prepared.current_price,
            ask=prepared.current_price,
            last=prepared.current_price,
        )

        # ── Convert authority TargetExposure to canonical TargetExposure ──
        from trading_agent.research.forecast import (
            TargetExposure as CanonicalTargetExposure,
        )

        canonical_target = CanonicalTargetExposure(
            symbol=symbol,
            exposure=finalized.target.target_exposure_pct,
            horizon=1,
            forecast_fingerprint=finalized.risk_decision.forecast_fingerprint,
            model_artifact_id=finalized.risk_decision.model_artifact_id,
            risk_decision_id=finalized.risk_decision.decision_id,
        )

        reservations_for_side = (
            0.0
            if prepared.signal == "SELL"
            else self.lifecycle.active_sell_reservations(symbol)
        )
        plan_result = self.execution_service.plan(
            target=canonical_target,
            risk_decision=finalized.risk_decision,
            observation=prepared.observation,
            portfolio=portfolio,
            price=price,
            existing_reservations=reservations_for_side,
        )
        if plan_result.status != OrderPlanningStatus.ORDER_REQUIRED:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=rule_id,
                idempotency_key=None,
                detail=f"planner status {plan_result.status.value}",
            )
        if plan_result.intent is None:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=rule_id,
                idempotency_key=None,
                detail="planner produced no intent",
            )

        intent = plan_result.intent
        side_lower = intent.side.lower()
        action = PlannedAction.REDUCTION if side_lower == "sell" else PlannedAction.INCREASE
        return PairOrderPlan(
            symbol=symbol,
            timeframe=timeframe,
            action=action,
            finalized=finalized,
            intent=intent,
            intent_id=intent.intent_id,
            side=intent.side,
            quantity=float(intent.quantity),
            limit_price=getattr(intent, "limit_price", None),
            instrument_rule_id=rule_id,
            idempotency_key=getattr(intent, "idempotency_key", None),
        )

    def submit_planned_order(self, plan: PairOrderPlan) -> PlannedSubmissionOutcome:
        """THE ONLY stage that performs broker I/O for one planned order.

        Mirrors the legacy single-pair submission sequence exactly:
        ExecutionAuthority validate → sell-protection cancel → gateway result
        → fill handling → BUY protective stop.
        """
        finalized = plan.finalized
        assert finalized is not None
        prepared = finalized.prepared
        intent = plan.intent
        assert intent is not None
        symbol = prepared.symbol

        assert self.planner is not None
        assert self.execution_authority is not None

        pair_rules = self.planner.rules_for(symbol)

        def _outcome(**kwargs: Any) -> PlannedSubmissionOutcome:
            return PlannedSubmissionOutcome(plan=plan, **kwargs)

        exec_input = ExecutionValidationInput(
            intent=intent,
            observation=prepared.observation,
            portfolio_state=CurrentPortfolioState(
                symbol=symbol,
                current_exposure=prepared.current_exposure,
                equity=prepared.equity,
                existing_quantity=prepared.current_quantity,
                available_cash=prepared.available_cash,
            ),
            price=MarketPrice(
                symbol=symbol,
                mid=prepared.current_price,
                bid=prepared.current_price,
                ask=prepared.current_price,
                last=prepared.current_price,
            ),
            instrument_rules=pair_rules,
            existing_reservations=(
                0.0
                if prepared.signal == "SELL"
                else self.lifecycle.active_sell_reservations(symbol)
            ),
            causation_chain=finalized.causation_chain,
            risk_decision=finalized.risk_decision,
        )

        exec_output = self.execution_authority.execute(exec_input)
        if not exec_output.allowed:
            logger.warning(
                "Order blocked by ExecutionAuthority [%s]: %s",
                symbol,
                exec_output.reason,
            )
            return _outcome(
                order=None,
                submit_state="BLOCKED_AUTHORITY",
                barrier=False,
                submitted=False,
            )

        # ── Protective order handling (sell signals) ────────────────
        canceled_protection_intents: list[str] = []
        if intent.side.lower() == "sell":
            cancel_ok, canceled_protection_intents = self._cancel_resting_protection(
                symbol
            )
            if not cancel_ok:
                # Legacy parity: submission already happened inside
                # ExecutionAuthority; report it without an Order wrapper so
                # lifecycle reconciliation owns the follow-up.
                broker_result_pre = exec_output.broker_result
                return _outcome(
                    order=self._result_to_order(
                        broker_result_pre, symbol, intent.side, intent.quantity
                    ),
                    submit_state=str(getattr(broker_result_pre.state, "value", broker_result_pre.state)),
                    barrier=is_execution_barrier(broker_result_pre),
                    submitted=True,
                )

        broker_result = exec_output.broker_result

        order = self._result_to_order(broker_result, symbol, intent.side, intent.quantity)

        # Check if fill was received
        order_state = self.lifecycle.state.orders.get(intent.intent_id)
        fill_received = order_state is not None and order_state.filled_size > 1e-12

        protection_submitted = False
        if (
            intent.side.lower() == "sell"
            and canceled_protection_intents
            and not fill_received
        ):
            self._mark_protection_gap(
                symbol,
                canceled_protection_intents,
                "exit was not filled after protective cancellation",
            )
        if fill_received and intent.side.lower() == "buy":
            position = self.exchange.get_position(symbol)
            protected_quantity = (
                position.quantity if position and position.is_active else 0.0
            )
            if protected_quantity <= 0:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker fill did not produce a positive protected quantity",
                )
                return _outcome(
                    order=order,
                    submit_state=str(getattr(broker_result.state, "value", broker_result.state)),
                    barrier=is_execution_barrier(broker_result),
                    submitted=True,
                    protection_submitted=False,
                )
            current_price = prepared.current_price
            plan_protection = ProtectionPlan(
                plan_id=f"prot_{intent.intent_id}",
                model_risk_decision_id=finalized.risk_decision.decision_id,
                symbol=intent.symbol,
                stop_type="stop_loss",
                stop_trigger=current_price * 0.95,
                take_profit=current_price * 1.10,
                state=ProtectionState.PROTECTION_REQUIRED,
                quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
                protected_quantity=protected_quantity,
            )
            protective_event = self.lifecycle.create_protective_order(
                symbol=plan_protection.symbol,
                kind=plan_protection.stop_type,
                trigger_price=plan_protection.stop_trigger,
                parent_intent_id=intent.intent_id,
            )
            protection_intent_id = f"{protective_event.aggregate_id}_submit"
            protection_result = self.execution_service.emergency_protection(
                EmergencyReduceRequest(
                    intent_id=protection_intent_id,
                    symbol=plan_protection.symbol,
                    side="sell",
                    quantity=plan_protection.protected_quantity,
                    reason="PROTECTIVE_STOP",
                    parent_intent_id=intent.intent_id,
                    idempotency_key=protection_intent_id,
                    metadata={
                        "order_type": "stop",
                        "stop_price": plan_protection.stop_trigger,
                        "time_in_force": "gtc",
                    },
                ),
                correlation_id=protection_intent_id,
            )
            if protection_result.success and protection_result.evidence:
                self.lifecycle.acknowledge_protective_order(
                    protective_order_id=protective_event.aggregate_id,
                    evidence=protection_result.evidence,
                )
                protection_submitted = True
            else:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker did not acknowledge the required protective order",
                )

        return _outcome(
            order=order,
            submit_state=str(getattr(broker_result.state, "value", broker_result.state)),
            barrier=is_execution_barrier(broker_result),
            submitted=True,
            protection_submitted=protection_submitted,
        )

    # ── Execute signals from Phase 2 agents ────────────────────────────

    def execute_strategy(
        self,
        strategy_runtime: StrategyRuntime,
        market_data: Any,
        observation: EnrichedMarketObservation | None = None,
    ) -> list[Order]:
        """Execute a promoted strategy through the full authority chain.

        N=1 composition over the SAME staged APIs MultiPairRuntime uses:
        _prepare_from_runtime → build_allocation_request → allocate
        → finalize_prepared_decision → plan_pair_order → submit_planned_order
        """
        if self.execution_service is None:
            raise RuntimeError(
                "execute_strategy requires instrument_rules to be provided at engine construction"
            )
        if self.resolver is None:
            raise RuntimeError(
                "execute_strategy requires RuntimeStrategyResolver (promotion_store + artifact_store)"
            )

        # Stage 1: resolve + StrategyOutput + DecisionAuthority (no I/O)
        prepared = self._prepare_from_runtime(strategy_runtime, market_data, observation)
        if prepared.prepare_status != "ok":
            return []

        # Stage 2: single-pair allocation (batch runtime uses allocate_batch)
        allocation_request = self.build_allocation_request(prepared)
        allocation_result = self.portfolio_allocator.allocate(allocation_request)
        if allocation_result.allocation_pct <= 0:
            logger.info(
                f"PortfolioAllocator returned zero allocation for "
                f"{prepared.symbol}: {allocation_result.reason}"
            )
            return []

        approved_target = allocation_result.target_exposure
        combined_chain = CausationChain(
            links=prepared.causation_chain.links
            + allocation_result.causation_chain.links
        )

        # Stage 3: ExposureAuthority validation of the allocated target
        finalized = self.finalize_prepared_decision(
            prepared,
            approved_target=approved_target,
            combined_chain=combined_chain,
        )
        if finalized is None:
            return []

        # Stage 4: planning (no broker I/O)
        plan = self.plan_pair_order(finalized)
        if plan.action not in (PlannedAction.REDUCTION, PlannedAction.INCREASE):
            return []

        # Stage 5: THE broker I/O step (+ inline post-fill protection)
        outcome = self.submit_planned_order(plan)
        return [outcome.order] if outcome.order is not None else []

'''

src = ENGINE.read_text()
start_idx = src.index(START_MARK)
end_idx = src.index(END_MARK)
new_src = src[:start_idx] + NEW + src[end_idx:]
ENGINE.write_text(new_src)
print(
    f"Replaced lines {src[:start_idx].count(chr(10)) + 1}..{src[:end_idx].count(chr(10)) + 1}"
)
print("New file line count:", new_src.count(chr(10)))
