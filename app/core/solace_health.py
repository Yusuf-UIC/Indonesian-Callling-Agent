import asyncio
import aiohttp
import logging
import subprocess
import sys
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SolaceHealthStatus:
    healthy: bool
    details: Dict[str, Any]
    error: Optional[str] = None


class SolaceHealthChecker:
    def __init__(
        self,
        host: str = "localhost",
        semp_port: int = 8008,
        mqtt_port: int = 1883,
        smf_port: int = 55555,
        username: str = "admin",
        password: str = "admin",
    ):
        self.host = host
        self.semp_port = semp_port
        self.mqtt_port = mqtt_port
        self.smf_port = smf_port
        self.username = username
        self.password = password
        self.base_url = f"http://{host}:{semp_port}"

    def _is_docker_running(self) -> bool:
        """Check if Docker daemon is accessible."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _is_solace_container_running(self) -> bool:
        """Check if Solace container is running."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=solace-broker", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return "solace-broker" in result.stdout
        except Exception:
            return False

    async def check_semp_api(self) -> SolaceHealthStatus:
        """Check Solace SEMP v2 management API."""
        # First check if Docker and container are running
        if not self._is_docker_running():
            return SolaceHealthStatus(
                healthy=False,
                details={"semp_api": "docker_not_running"},
                error="Docker daemon not accessible"
            )
        
        if not self._is_solace_container_running():
            return SolaceHealthStatus(
                healthy=False,
                details={"semp_api": "container_not_running"},
                error="Solace container not running"
            )
        
        url = f"{self.base_url}/SEMP/v2/config/msgVpns/default"
        auth = aiohttp.BasicAuth(self.username, self.password)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return SolaceHealthStatus(
                            healthy=True,
                            details={"semp_api": "ok", "vpn_status": data.get("data", [{}])[0].get("enabled", "unknown")}
                        )
                    else:
                        return SolaceHealthStatus(
                            healthy=False,
                            details={"semp_api": f"http_{resp.status}"},
                            error=f"SEMP API returned status {resp.status}"
                        )
        except asyncio.TimeoutError:
            return SolaceHealthStatus(
                healthy=False,
                details={"semp_api": "timeout"},
                error="SEMP API timeout"
            )
        except Exception as e:
            return SolaceHealthStatus(
                healthy=False,
                details={"semp_api": "error"},
                error=str(e)
            )

    async def check_mqtt_port(self) -> SolaceHealthStatus:
        """Check MQTT port connectivity."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.mqtt_port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            return SolaceHealthStatus(
                healthy=True,
                details={"mqtt_port": "open"}
            )
        except Exception as e:
            return SolaceHealthStatus(
                healthy=False,
                details={"mqtt_port": "closed"},
                error=str(e)
            )

    async def check_smf_port(self) -> SolaceHealthStatus:
        """Check SMF port connectivity."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.smf_port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            return SolaceHealthStatus(
                healthy=True,
                details={"smf_port": "open"}
            )
        except Exception as e:
            return SolaceHealthStatus(
                healthy=False,
                details={"smf_port": "closed"},
                error=str(e)
            )

    async def check_all(self) -> Dict[str, SolaceHealthStatus]:
        """Run all health checks."""
        results = {}
        
        # Run checks in parallel
        semp_task = asyncio.create_task(self.check_semp_api())
        mqtt_task = asyncio.create_task(self.check_mqtt_port())
        smf_task = asyncio.create_task(self.check_smf_port())
        
        results["semp_api"] = await semp_task
        results["mqtt_port"] = await mqtt_task
        results["smf_port"] = await smf_task
        
        return results

    async def wait_for_healthy(self, timeout: int = 120, interval: int = 5) -> bool:
        """Wait until all critical health checks pass."""
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            results = await self.check_all()
            
            # Critical: SEMP API must be healthy
            semp_healthy = results.get("semp_api", SolaceHealthStatus(healthy=False, details={})).healthy
            
            if semp_healthy:
                logger.info("Solace broker is healthy and ready")
                return True
            
            logger.info(f"Waiting for Solace broker... ({asyncio.get_event_loop().time() - start_time:.0f}s)")
            await asyncio.sleep(interval)
        
        logger.error(f"Solace broker did not become healthy within {timeout}s")
        return False


async def quick_health_check() -> bool:
    """Quick health check for use in scripts."""
    checker = SolaceHealthChecker()
    results = await checker.check_all()
    
    print("=" * 60)
    print("Solace Broker Health Check")
    print("=" * 60)
    
    all_healthy = True
    for name, status in results.items():
        status_str = "HEALTHY" if status.healthy else "UNHEALTHY"
        print(f"  {name}: {status_str}")
        if status.error:
            print(f"    Error: {status.error}")
        if not status.healthy:
            all_healthy = False
    
    print("=" * 60)
    return all_healthy


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Solace Broker Health Check")
    parser.add_argument("--wait", action="store_true", help="Wait until healthy")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout for wait")
    args = parser.parse_args()

    checker = SolaceHealthChecker()
    
    if args.wait:
        success = await checker.wait_for_healthy(timeout=args.timeout)
        exit(0 if success else 1)
    else:
        success = await quick_health_check()
        exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())