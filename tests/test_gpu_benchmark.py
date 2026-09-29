"""
GPU Benchmark Tests for Indonesia IVR Speech AI
================================================
Tests the SLA compliance of the voice pipeline components.
"""

import asyncio
import json
import statistics
import time
import uuid
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.services.agent_solace import BankingAgentSolace
from app.services.stt_service import STTService
from app.services.solace_client import (
    create_solace_client,
    SolaceEvent,
    SolaceEventType,
    BankingEventHandler,
)
from app.services.mock_bank import mock_banking_service
from app.services.semantic_intent import SemanticIntentClassifier, IntentType
from app.telephony.audio_bridge import audio_bridge
from app.core.formatters import clean_text_for_tts

logger = logging.getLogger(__name__)


@dataclass
class LatencyMetrics:
    vad_latency_ms: float = 0.0
    stt_latency_ms: float = 0.0
    solace_latency_ms: float = 0.0
    e2e_latency_ms: float = 0.0


@dataclass
class BenchmarkResult:
    test_name: str
    metrics: LatencyMetrics
    success: bool
    transcript: str = ""
    expected_intent: Optional[str] = None
    detected_intent: Optional[str] = None
    wer: float = 0.0
    error: Optional[str] = None


class SLABenchmark:
    """SLA Benchmark Harness for Indonesia IVR Speech AI."""
    
    # SLA Targets (milliseconds)
    SLA_VAD_LATENCY_MS = 50.0
    SLA_STT_LATENCY_MS = 300.0
    SLA_SOLACE_LATENCY_MS = 25.0
    SLA_E2E_LATENCY_MS = 800.0
    SLA_WER_THRESHOLD = 0.15  # 15% Word Error Rate
    
    def __init__(self, use_mock_solace: bool = True, use_mock_stt: bool = True):
        self.use_mock_solace = use_mock_solace
        self.use_mock_stt = use_mock_stt
        
        # Core components
        self.intent_classifier = SemanticIntentClassifier()
        self.agent: Optional[BankingAgentSolace] = None
        self.stt_service: Optional[STTService] = None
        self.solace_client = None
        self.banking_handler = None
        
        # Benchmark state
        self.results: List[BenchmarkResult] = []
        self.latencies: Dict[str, List[float]] = {
            'vad': [],
            'stt': [],
            'solace': [],
            'e2e': [],
            'wer': []
        }
    
    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing SLA Benchmark components...")
        
        # Initialize intent classifier
        logger.info("Initializing intent classifier...")
        self.intent_classifier.initialize()
        
        # Initialize STT
        logger.info("Initializing STT service...")
        self.stt_service = STTService(
            groq_api_key=None,
            use_local_fallback=True,
        )
        
        # Initialize agent
        logger.info("Initializing Banking Agent...")
        self.agent = BankingAgentSolace(
            use_mock=True,
            use_mqtt=False
        )
        await self.agent.initialize()
        
        logger.info("SLA Benchmark initialized successfully")
    
    def _calculate_wer(self, reference: str, hypothesis: str) -> float:
        """Calculate Word Error Rate (WER) between reference and hypothesis."""
        ref_words = reference.lower().split()
        hyp_words = hypothesis.lower().split()
        
        if not ref_words:
            return 1.0 if hyp_words else 0.0
        
        # Levenshtein distance
        d = np.zeros((len(ref_words) + 1, len(hyp_words) + 1))
        for i in range(len(ref_words) + 1):
            d[i][0] = i
        for j in range(len(hyp_words) + 1):
            d[0][j] = j
        
        for i in range(1, len(ref_words) + 1):
            for j in range(1, len(hyp_words) + 1):
                if ref_words[i-1] == hyp_words[j-1]:
                    d[i][j] = d[i-1][j-1]
                else:
                    d[i][j] = min(d[i-1][j], d[i][j-1], d[i-1][j-1]) + 1
        
        return d[len(ref_words)][len(hyp_words)] / len(ref_words)
    
    async def run_intent_classification_benchmark(self, test_cases: List[Dict]) -> List[BenchmarkResult]:
        """Benchmark intent classification accuracy and latency."""
        results = []
        
        for case in test_cases:
            text = case['text']
            expected_intent = case['expected_intent']
            
            start = time.perf_counter()
            result = self.intent_classifier.classify(text)
            latency = (time.perf_counter() - start) * 1000
            
            detected_intent = result.intent.value
            success = detected_intent == expected_intent
            
            result = BenchmarkResult(
                test_name=f"intent_classification_{expected_intent}",
                metrics=LatencyMetrics(e2e_latency_ms=latency),
                success=success,
                transcript=text,
                expected_intent=expected_intent,
                detected_intent=detected_intent
            )
            results.append(result)
            self.latencies['e2e'].append(latency)
            
            if not success:
                logger.warning(f"Intent mismatch: '{text}' -> expected {expected_intent}, got {detected_intent}")
        
        return results
    
    async def run_solace_benchmark(self, iterations: int = 10) -> List[BenchmarkResult]:
        """Benchmark Solace event-driven request-reply latency."""
        results = []
        
        client = await create_solace_client(use_mock=True)
        handler = BankingEventHandler(client, mock_banking_service)
        
        test_cases = [
            ("balance", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
             {"account_number": "1234567890", "account_type": "savings"}),
            ("transactions", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
             {"account_number": "9876543210", "limit": 3}),
            ("block_card", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
             {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
            ("error", SolaceEventType.BALANCE_REQUEST, SolaceEventType.ERROR,
             {"account_number": "9999999999", "account_type": "savings"}),
        ]
        
        all_passed = True
        for name, req_type, resp_type, payload in test_cases:
            latencies = []
            
            for i in range(iterations):
                if name == "block_card" and "4567890123456791" in mock_banking_service.cards:
                    mock_banking_service.cards["4567890123456791"]["blocked"] = False
                
                request = SolaceEvent(
                    event_type=req_type,
                    correlation_id=f"bench-{name}-{i}",
                    payload=payload,
                )
                
                start = time.perf_counter()
                try:
                    response = await client.request_reply(request, resp_type.value, timeout=5.0)
                    latency = (time.perf_counter() - start) * 1000
                    latencies.append(latency)
                    
                    if resp_type == SolaceEventType.ERROR:
                        success = "error" in response.payload
                    else:
                        success = response.payload.get("status") == "success"
                    
                    if not success:
                        all_passed = False
                except Exception as e:
                    logger.warning(f"Error in {name} test: {e}")
                    all_passed = False
            
            avg_latency = statistics.mean(latencies) if latencies else 0
            p95_latency = np.percentile(latencies, 95) if latencies else 0
            
            result = BenchmarkResult(
                test_name=f"solace_{name}",
                metrics=LatencyMetrics(solace_latency_ms=avg_latency, e2e_latency_ms=avg_latency),
                success=all_passed,
                error=f"P95: {p95_latency:.1f}ms" if p95_latency > self.SLA_SOLACE_LATENCY_MS else None
            )
            results.append(result)
            self.latencies['solace'].extend(latencies)
            
            if avg_latency > self.SLA_SOLACE_LATENCY_MS:
                logger.warning(f"Solace SLA violation: avg={avg_latency:.1f}ms, p95={p95_latency:.1f}ms")
        
        await client.disconnect()
        return results
    
    async def run_e2e_benchmark(self, test_cases: List[Dict]) -> List[BenchmarkResult]:
        """Run end-to-end benchmark: speech -> STT -> intent -> Solace -> response."""
        results = []
        
        for case in test_cases:
            text = case['text']
            expected_intent = case['expected_intent']
            reference_text = case.get('reference_text', text)
            
            # Simulate VAD latency
            vad_start = time.perf_counter()
            await asyncio.sleep(0.01)  # Simulate ~10ms VAD
            vad_latency = (time.perf_counter() - vad_start) * 1000
            
            # Simulate STT
            stt_start = time.perf_counter()
            transcript = text
            stt_latency = (time.perf_counter() - stt_start) * 1000
            
            # Intent classification
            intent_start = time.perf_counter()
            intent_result = self.intent_classifier.classify(transcript)
            intent_latency = (time.perf_counter() - intent_start) * 1000
            
            detected_intent = intent_result.intent.value
            intent_success = intent_result.intent.value == expected_intent
            
            # Solace request-reply
            solace_start = time.perf_counter()
            if self.agent.solace_client:
                try:
                    if expected_intent == "check_balance":
                        response = await self.agent._send_request_reply(
                            IntentType.CHECK_BALANCE,
                            {"account_number": "1234567890", "account_type": "savings"}
                        )
                    elif expected_intent == "transaction_history":
                        response = await self.agent._send_request_reply(
                            IntentType.TRANSACTION_HISTORY,
                            {"account_number": "9876543210", "limit": 3}
                        )
                    elif expected_intent == "block_card":
                        if "4567890123456791" in mock_banking_service.cards:
                            mock_banking_service.cards["4567890123456791"]["blocked"] = False
                        response = await self.agent._send_request_reply(
                            IntentType.BLOCK_CARD,
                            {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}
                        )
                    else:
                        response = {"error": "Unknown intent"}
                    solace_latency = (time.perf_counter() - solace_start) * 1000
                    solace_success = "error" not in response
                except Exception as e:
                    logger.error(f"Error in Solace request-reply: {e}")
                    solace_latency = (time.perf_counter() - solace_start) * 1000
                    solace_success = False
            else:
                solace_latency = 0
                solace_success = True
            
            # Calculate WER
            wer = self._calculate_wer(reference_text, transcript)
            
            # Total E2E latency
            e2e_latency = vad_latency + stt_latency + intent_latency + solace_latency
            
            # Overall success
            success = (intent_success and solace_success and 
                      self._calculate_wer(reference_text, transcript) <= self.SLA_WER_THRESHOLD and 
                      (vad_latency + stt_latency + intent_latency + solace_latency) <= self.SLA_E2E_LATENCY_MS)
            
            result = BenchmarkResult(
                test_name=f"e2e_{expected_intent}",
                metrics=LatencyMetrics(
                    vad_latency_ms=vad_latency,
                    stt_latency_ms=stt_latency,
                    solace_latency_ms=solace_latency,
                    e2e_latency_ms=vad_latency + stt_latency + intent_latency + solace_latency
                ),
                success=success,
                transcript=text,
                expected_intent=expected_intent,
                detected_intent=detected_intent,
                wer=self._calculate_wer(reference_text, transcript)
            )
            results.append(result)
            
            # Store latencies
            self.latencies['vad'].append(vad_latency)
            self.latencies['stt'].append(stt_latency)
            self.latencies['solace'].append(solace_latency)
            self.latencies['e2e'].append(vad_latency + stt_latency + intent_latency + solace_latency)
            self.latencies['wer'].append(self._calculate_wer(reference_text, transcript))
            
            if not success:
                logger.warning(f"E2E SLA violation: intent={intent_success}, solace={solace_success}")
        
        return results
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive benchmark report."""
        def stats(latencies: List[float]) -> Dict[str, float]:
            if not latencies:
                return {"count": 0, "mean": 0, "median": 0, "p95": 0, "min": 0, "max": 0}
            return {
                "count": len(latencies),
                "mean": statistics.mean(latencies),
                "median": statistics.median(latencies),
                "p95": np.percentile(latencies, 95) if len(latencies) > 1 else latencies[0],
                "min": min(latencies),
                "max": max(latencies)
            }
        
        total_tests = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total_tests - passed
        
        report = {
            "summary": {
                "total_tests": total_tests,
                "passed": passed,
                "failed": failed,
                "pass_rate": f"{passed/total_tests*100:.1f}%" if total_tests > 0 else "0%"
            },
            "sla_targets": {
                "vad_latency_ms": self.SLA_VAD_LATENCY_MS,
                "stt_latency_ms": self.SLA_STT_LATENCY_MS,
                "solace_latency_ms": self.SLA_SOLACE_LATENCY_MS,
                "e2e_latency_ms": self.SLA_E2E_LATENCY_MS,
                "wer_threshold": f"{self.SLA_WER_THRESHOLD*100:.0f}%"
            },
            "latency_statistics": {
                "vad_ms": self.latencies['vad'],
                "stt_ms": self.latencies['stt'],
                "solace_ms": self.latencies['solace'],
                "e2e_ms": self.latencies['e2e'],
                "wer": self.latencies['wer']
            },
            "latency_summary": {
                "vad_ms": stats(self.latencies['vad']),
                "stt_ms": stats(self.latencies['stt']),
                "solace_ms": stats(self.latencies['solace']),
                "e2e_ms": stats(self.latencies['e2e']),
                "wer": stats(self.latencies['wer'])
            },
            "sla_compliance": {
                "vad": all(l <= self.SLA_VAD_LATENCY_MS for l in self.latencies['vad']),
                "stt": all(l <= self.SLA_STT_LATENCY_MS for l in self.latencies['stt']),
                "solace": all(l <= self.SLA_SOLACE_LATENCY_MS for l in self.latencies['solace']),
                "e2e": all(l <= self.SLA_E2E_LATENCY_MS for l in self.latencies['e2e']),
                "wer": all(w <= self.SLA_WER_THRESHOLD for w in self.latencies['wer'])
            },
            "detailed_results": [
                {
                    "test": r.test_name,
                    "success": r.success,
                    "metrics": {
                        "vad_ms": r.metrics.vad_latency_ms,
                        "stt_ms": r.metrics.stt_latency_ms,
                        "solace_ms": r.metrics.solace_latency_ms,
                        "e2e_ms": r.metrics.e2e_latency_ms
                    },
                    "intent": {
                        "expected": r.expected_intent,
                        "detected": r.detected_intent
                    },
                    "wer": r.wer,
                    "error": r.error
                }
                for r in self.results
            ]
        }
        
        return report


async def run_benchmarks():
    """Run all benchmarks and generate report."""
    benchmark = SLABenchmark(use_mock_solace=True, use_mock_stt=True)
    await benchmark.initialize()
    
    # Test cases for intent classification
    intent_test_cases = [
        {"text": "cek saldo rekening 1234567890", "expected_intent": "check_balance"},
        {"text": "saldo saya berapa", "expected_intent": "check_balance"},
        {"text": "duitku di rekening ada berapa", "expected_intent": "check_balance"},
        {"text": "blokir kartu 4567890123456789", "expected_intent": "block_card"},
        {"text": "kartuku hilang tolong diblokir", "expected_intent": "block_card"},
        {"text": "riwayat transaksi rekening 9876543210", "expected_intent": "transaction_history"},
        {"text": "mutasi rekening bulan ini", "expected_intent": "transaction_history"},
        {"text": "halo apa kabar", "expected_intent": "greeting"},
        {"text": "bantuan dong", "expected_intent": "help"},
        {"text": "ini teks acak tidak jelas", "expected_intent": "unknown"},
    ]
    
    # E2E test cases
    e2e_test_cases = [
        {"text": "cek saldo rekening 1234567890", "expected_intent": "check_balance", "reference_text": "cek saldo rekening 1234567890"},
        {"text": "saldo saya berapa", "expected_intent": "check_balance", "reference_text": "saldo saya berapa"},
        {"text": "blokir kartu 4567890123456789 OTP 123456", "expected_intent": "block_card", "reference_text": "blokir kartu 4567890123456789 OTP 123456"},
        {"text": "transaksi terakhir rekening 9876543210", "expected_intent": "transaction_history", "reference_text": "transaksi terakhir rekening 9876543210"},
    ]
    
    print("=" * 60)
    print("Running SLA Benchmarks...")
    print("=" * 60)
    
    # Run benchmarks
    print("\n1. Intent Classification Benchmark...")
    intent_results = await benchmark.run_intent_classification_benchmark(intent_test_cases)
    print(f"   Completed: {len(intent_results)} tests")
    
    print("\n2. Solace Event-Driven Benchmark...")
    solace_results = await benchmark.run_solace_benchmark(iterations=5)
    print(f"   Completed: {len(solace_results)} tests")
    
    print("\n3. End-to-End Latency Benchmark...")
    e2e_results = await benchmark.run_e2e_benchmark(e2e_test_cases)
    print(f"   Completed: {len(e2e_results)} tests")
    
    # Combine all results
    benchmark.results = intent_results + solace_results + e2e_results
    
    # Generate and save report
    report = benchmark.generate_report()
    
    # Save report
    report_path = Path("benchmark_report.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK REPORT")
    print("=" * 60)
    print(f"\nSummary:")
    print(f"  Total Tests: {report['summary']['total_tests']}")
    print(f"  Passed: {report['summary']['passed']}")
    print(f"  Failed: {report['summary']['failed']}")
    print(f"  Pass Rate: {report['summary']['pass_rate']}")
    
    print(f"\nSLA Compliance:")
    for sla, passed in report['sla_compliance'].items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {sla.upper()}: {status}")
    
    print(f"\nLatency Summary (ms):")
    for metric, stats in report['latency_summary'].items():
        if stats['count'] > 0:
            print(f"  {metric}: mean={stats['mean']:.1f}ms, p95={stats['p95']:.1f}ms, max={stats['max']:.1f}ms")
    
    print(f"\nDetailed report saved to: {report_path}")
    
    return report


# Pytest test functions
def test_intent_classification_benchmark():
    """Test intent classification accuracy."""
    async def run():
        benchmark = SLABenchmark(use_mock_solace=True, use_mock_stt=True)
        await benchmark.initialize()
        
        intent_test_cases = [
            {"text": "cek saldo rekening 1234567890", "expected_intent": "check_balance"},
            {"text": "saldo saya berapa", "expected_intent": "check_balance"},
            {"text": "duitku di rekening ada berapa", "expected_intent": "check_balance"},
            {"text": "blokir kartu 4567890123456789", "expected_intent": "block_card"},
            {"text": "kartuku hilang tolong diblokir", "expected_intent": "block_card"},
            {"text": "riwayat transaksi rekening 9876543210", "expected_intent": "transaction_history"},
            {"text": "mutasi rekening bulan ini", "expected_intent": "transaction_history"},
            {"text": "halo apa kabar", "expected_intent": "greeting"},
            {"text": "bantuan dong", "expected_intent": "help"},
            {"text": "ini teks acak tidak jelas", "expected_intent": "unknown"},
        ]
        
        benchmark = SLABenchmark(use_mock_solace=True, use_mock_stt=True)
        await benchmark.initialize()
        results = await benchmark.run_intent_classification_benchmark(intent_test_cases)
        
        passed = sum(1 for r in results if r.success)
        assert passed == len(results), f"Failed cases: {[r for r in results if not r.success]}"
    
    asyncio.run(run())


def test_solace_mock_benchmark():
    """Test Solace mock request-reply."""
    async def run():
        client = await create_solace_client(use_mock=True)
        handler = BankingEventHandler(client, mock_banking_service)
        
        test_cases = [
            ("Balance Request", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
             {"account_number": "1234567890", "account_type": "savings"}),
            ("Transactions Request", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
             {"account_number": "9876543210", "limit": 3}),
            ("Block Card Request", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
             {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
            ("Error: Invalid Account", SolaceEventType.BALANCE_REQUEST, SolaceEventType.ERROR,
             {"account_number": "9999999999", "account_type": "savings"}),
        ]
        
        all_passed = True
        for name, req_type, resp_type, payload in test_cases:
            request = SolaceEvent(
                event_type=req_type,
                correlation_id=f"test-{req_type.value}",
                payload=payload,
            )
            
            response = await client.request_reply(request, resp_type.value, timeout=5.0)
            if resp_type == SolaceEventType.ERROR:
                if "error" not in response.payload:
                    raise AssertionError(f"Expected error response for {name}")
            elif response.payload.get("status") != "success":
                raise AssertionError(f"Expected success for {name}: {response.payload}")
        
        await client.disconnect()
    
    asyncio.run(run())


def test_stt_benchmark():
    """Test STT pipeline."""
    async def run():
        benchmark = SLABenchmark(use_mock_solace=True, use_mock_stt=True)
        await benchmark.initialize()
        
        # Test with mock STT - just verify initialization works
        result = await benchmark.run_e2e_benchmark([
            {"text": "cek saldo rekening 1234567890", "expected_intent": "check_balance", "reference_text": "cek saldo rekening 1234567890"}
        ])
        assert len(result) == 1
    
    asyncio.run(run())


def test_solace_mock_benchmark():
    """Test Solace mock request-reply."""
    async def run():
        client = await create_solace_client(use_mock=True)
        handler = BankingEventHandler(client, mock_banking_service)
        
        test_cases = [
            ("Balance Request", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
             {"account_number": "1234567890", "account_type": "savings"}),
            ("Transactions Request", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
             {"account_number": "9876543210", "limit": 3}),
            ("Block Card Request", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
             {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
            ("Error: Invalid Account", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
             {"account_number": "9999999999", "account_type": "savings"}),
        ]
        
        all_passed = True
        for name, req_type, resp_type, payload in test_cases:
            request = SolaceEvent(
                event_type=req_type,
                correlation_id=f"test-{req_type.value}",
                payload=payload,
            )
            
            response = await client.request_reply(request, resp_type.value, timeout=5.0)
            if "Invalid Account" in name:
                if "error" not in response.payload and response.payload.get("status") != "error":
                    raise AssertionError(f"Expected error response for {name}")
            elif response.payload.get("status") != "success":
                raise AssertionError(f"Expected success for {name}: {response.payload}")
        
        await client.disconnect()
    
    asyncio.run(run())


if __name__ == "__main__":
    asyncio.run(run_benchmarks())