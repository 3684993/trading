import psutil
import requests
from typing import Dict, Optional
from datetime import datetime
from core.logger import logger
from config.settings import settings


class HealthMonitor:
    def __init__(self):
        self.checks: Dict[str, bool] = {}
        self.last_check_time: Optional[datetime] = None
        logger.info("HealthMonitor initialized")
    
    def check_all(self) -> Dict:
        try:
            self.last_check_time = datetime.now()
            
            self.checks = {
                "exchange_api": self._check_exchange_api(),
                "proxy_connection": self._check_proxy(),
                "memory_usage": self._check_memory(),
                "ai_service": self._check_ai_service()
            }
            
            return self.get_status()
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return {"status": "error", "message": str(e)}
    
    def _check_exchange_api(self) -> bool:
        try:
            base_url = settings.binance_testnet_url or "https://demo-fapi.binance.com"
            
            proxies = None
            if settings.socks5_proxy:
                proxy_url = settings.socks5_proxy
                if proxy_url.startswith("socks5://"):
                    proxy_url = proxy_url.replace("socks5://", "socks5h://")
                proxies = {
                    "http": proxy_url,
                    "https": proxy_url
                }
            
            response = requests.get(
                f"{base_url}/fapi/v1/ping",
                proxies=proxies,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("Exchange API check: OK")
                return True
            else:
                logger.warning(f"Exchange API check: Failed ({response.status_code})")
                return False
                
        except Exception as e:
            logger.error(f"Exchange API check error: {e}")
            return False
    
    def _check_proxy(self) -> bool:
        try:
            if not settings.socks5_proxy:
                logger.info("Proxy check: Not configured")
                return True
            
            proxy_url = settings.socks5_proxy
            if proxy_url.startswith("socks5://"):
                proxy_url = proxy_url.replace("socks5://", "socks5h://")
            
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
            
            response = requests.get(
                "https://api.binance.com/api/v3/ping",
                proxies=proxies,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("Proxy check: OK")
                return True
            else:
                logger.warning(f"Proxy check: Failed ({response.status_code})")
                return False
                
        except Exception as e:
            logger.error(f"Proxy check error: {e}")
            return False
    
    def _check_memory(self) -> bool:
        try:
            memory = psutil.virtual_memory()
            memory_pct = memory.percent
            
            if memory_pct > 90:
                logger.warning(f"Memory usage high: {memory_pct}%")
                return False
            else:
                logger.info(f"Memory usage: {memory_pct}%")
                return True
                
        except Exception as e:
            logger.error(f"Memory check error: {e}")
            return False
    
    def _check_ai_service(self) -> bool:
        try:
            base_url = settings.openai_base_url or "http://127.0.0.1:1234/v1"
            
            response = requests.get(
                f"{base_url.replace('/v1', '')}/v1/models",
                timeout=5
            )
            
            if response.status_code == 200:
                logger.info("AI service check: OK")
                return True
            else:
                logger.warning(f"AI service check: Failed ({response.status_code})")
                return False
                
        except Exception as e:
            logger.warning(f"AI service check: Not available ({e})")
            return False
    
    def get_status(self) -> Dict:
        try:
            all_healthy = all(self.checks.values())
            
            return {
                "status": "healthy" if all_healthy else "degraded",
                "timestamp": self.last_check_time.isoformat() if self.last_check_time else None,
                "checks": self.checks,
                "memory_usage": self._get_memory_details()
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def _get_memory_details(self) -> Dict:
        try:
            memory = psutil.virtual_memory()
            return {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "used_pct": memory.percent
            }
        except Exception:
            return {}
    
    def print_status(self):
        try:
            status = self.get_status()
            
            print("\n" + "="*60)
            print("SYSTEM HEALTH STATUS")
            print("="*60)
            
            print(f"\nOverall Status: {status.get('status', 'unknown').upper()}")
            print(f"Last Check: {status.get('timestamp', 'N/A')}")
            
            print("\nChecks:")
            checks = status.get("checks", {})
            for check_name, result in checks.items():
                status_icon = "✓" if result else "✗"
                print(f"  {status_icon} {check_name}: {'OK' if result else 'FAILED'}")
            
            memory = status.get("memory_usage", {})
            if memory:
                print(f"\nMemory:")
                print(f"  Total: {memory.get('total_gb', 0):.2f} GB")
                print(f"  Available: {memory.get('available_gb', 0):.2f} GB")
                print(f"  Used: {memory.get('used_pct', 0):.1f}%")
            
            print("\n" + "="*60)
            
        except Exception as e:
            print(f"Print status error: {e}")
    
    def format_log(self) -> str:
        try:
            status = self.get_status()
            checks = status.get("checks", {})
            
            return (
                f"Health: {status.get('status', 'unknown')} | "
                f"Exchange={checks.get('exchange_api', False)} | "
                f"Proxy={checks.get('proxy_connection', False)} | "
                f"Memory={checks.get('memory_usage', False)}"
            )
        except Exception:
            return "Health: unknown"
