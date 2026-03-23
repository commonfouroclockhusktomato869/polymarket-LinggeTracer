# core_radar.py
import os
import time
import requests
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

class PolymarketAnalyzer:
    def __init__(self, proxy_port=None):
        self.base_url = "https://data-api.polymarket.com"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if proxy_port:
            self.session.proxies.update({
                'http': f'http://127.0.0.1:{proxy_port}',
                'https': f'http://127.0.0.1:{proxy_port}'
            })

    # 修改 core_radar.py 中的这两个方法
    def fetch_trades(self, wallet_address, max_pages=50, progress_callback=None):
        all_trades = []
        limit = 100
        cursor = None
        current_endpoint = "/trades"

        for page in range(1, max_pages + 1):
            url = f"{self.base_url}{current_endpoint}?user={wallet_address}&limit={limit}"
            url += f"&cursor={cursor}" if cursor else f"&offset={(page - 1) * limit}"
            
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    trades = data.get("data", []) if isinstance(data, dict) else data
                    if not trades: break
                    all_trades.extend(trades)
                    
                    # 💡 【核心新增】触发实时进度播报！
                    if progress_callback:
                        progress_callback(f"⏳ 正在深度挖掘链上数据...\n当前进度：第 {page} 页，已抓取 {len(all_trades)} 条记录 📥")
                    
                    if isinstance(data, dict) and data.get("next_cursor"):
                        cursor = data.get("next_cursor")
                    else:
                        cursor = None
                        
                    if len(trades) < limit: break
                else:
                    break
            except Exception as e:
                break
            time.sleep(0.5)
            
        return all_trades

    def generate_ai_summary(self, wallet_address, max_pages=50, progress_callback=None):
        """传递 callback"""
        # 💡 在开始前播报一下
        if progress_callback:
            progress_callback(f"🔍 锁定目标钱包: {wallet_address[:8]}...\n🚀 正在连接 Polymarket 底层节点，准备抓取...")
            
        raw_trades = self.fetch_trades(wallet_address, max_pages, progress_callback)
        
        if not raw_trades:
            return f"未能抓取到钱包 {wallet_address} 的交易数据。"
            
        # 💡 抓取完毕，通知进入 AI 分析阶段
        if progress_callback:
            progress_callback(f"✅ 数据抓取完毕，共 {len(raw_trades)} 条！\n🧠 正在进行高维特征降维，唤醒 AI 投研大脑进行深度分析，请稍候...")
            
    
        # 数据清洗
        records = []
        for t in raw_trades:
            side = str(t.get('side', '')).upper()
            size = float(t.get('size', 0))
            price = float(t.get('price', 0))
            if size <= 0 or price <= 0: continue
            
            records.append({
                'market': t.get('title') or t.get('market') or 'Unknown',
                'action': 'Buy' if side in ('BUY', '1') else ('Sell' if side in ('SELL', '0') else side),
                'price_odds': price,
                'usdt_volume': round(size * price, 2)
            })

        df = pd.DataFrame(records)
        if df.empty:
            return "抓取成功，但没有找到有效的交易记录。"

        # 浓缩统计特征 (Data Reduction for LLM Context)
        total_trades = len(df)
        total_volume = df['usdt_volume'].sum()
        avg_volume = df['usdt_volume'].mean()
        max_volume = df['usdt_volume'].max()
        
        action_counts = df['action'].value_counts().to_dict()
        top_markets = df.groupby('market')['usdt_volume'].sum().sort_values(ascending=False).head(3).to_dict()
        
        # 赔率偏好统计
        def categorize(p):
            if p >= 0.8: return '极高胜率(>=80%)'
            if p >= 0.5: return '优势方(50-80%)'
            if p >= 0.2: return '劣势高赔(20-50%)'
            return '摸奖极小概率(<20%)'
            
        df['odds_pref'] = df['price_odds'].apply(categorize)
        odds_dist = df['odds_pref'].value_counts().to_dict()

        # 组装为 LLM 易读的 JSON/文本格式
        summary = f"""
### 钱包 {wallet_address} 链上行为特征摘要：
- **总交易笔数**: {total_trades} 笔
- **总资金吞吐量 (USDT)**: ${total_volume:,.2f}
- **平均单笔下注**: ${avg_volume:,.2f}
- **历史最大单笔重仓**: ${max_volume:,.2f}
- **买卖行为分布**: {action_counts}
- **赔率偏好分布**: {odds_dist}
- **最重仓的三大市场**: 
"""
        for m, v in top_markets.items():
            summary += f"  - [{m}]: 投入 ${v:,.2f}\n"

        return summary
