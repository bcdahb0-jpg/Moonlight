import { useEffect, useState, type ReactElement } from 'react';
import { agentApi } from '@/api/rest';
import { Icon } from '@/ui/icons';

export function AgentSettings(): ReactElement {
  const [useMcpp, setUseMcpp] = useState(false);
  const [status, setStatus] = useState('');

  useEffect(() => {
    void agentApi
      .get()
      .then((r) => setUseMcpp(r.use_mcpp))
      .catch((err) => setStatus(err instanceof Error ? err.message : '加载失败'));
  }, []);

  const toggle = async (value: boolean): Promise<void> => {
    setUseMcpp(value);
    try {
      const r = await agentApi.save({ use_mcpp: value });
      setStatus(r.restart_required ? '已保存，需要重启或切换角色后生效' : '已保存');
    } catch (err) {
      setUseMcpp(!value);
      setStatus(err instanceof Error ? err.message : '保存失败');
    }
  };

  return (
    <div className="settings-section general-settings">
      <section className="settings-card">
        <div className="settings-card-head">
          <span className="settings-card-icon">
            <Icon name="tool" size={16} />
          </span>
          <div className="settings-card-title">
            <h3>Agent 工具（MCP）</h3>
            <p>让 LLM 通过函数调用使用外部工具</p>
          </div>
        </div>
        <div className="settings-card-body">
          <label className="toggle-row">
            <span>启用 MCP 工具调用</span>
            <input
              type="checkbox"
              checked={useMcpp}
              onChange={(e) => void toggle(e.target.checked)}
            />
          </label>
          <div className="setting-note">
            开启后 LLM 可通过函数调用使用工具（如联网搜索、查询时间等），需要后端在 `agent_settings.basic_memory_agent.use_mcpp` 中启用对应 MCP 服务器。
          </div>
          {status && <div className="setting-status">{status}</div>}
        </div>
      </section>
    </div>
  );
}
