import { useState, useEffect, useRef, useCallback } from 'react';
import { Layout, Menu, Typography, Checkbox, Button, Card, Row, Col, List, Statistic, Empty, Tag, Space, Modal } from 'antd';
import {
  HomeOutlined,
  UnorderedListOutlined,
  LoadingOutlined,
  BarChartOutlined,
  PlayCircleOutlined,
  LeftOutlined,
  RightOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import { agentApi, getImageUrl, type AgentCase, type Citation, type ScreenshotItem, type StartMode } from './api/agent';

const { Sider, Content } = Layout;
const { Title, Paragraph, Text } = Typography;

const menuItems = [
  { key: 'welcome', icon: <HomeOutlined />, label: '欢迎' },
  { key: 'cases', icon: <UnorderedListOutlined />, label: '测试用例列表' },
  { key: 'ongoing', icon: <LoadingOutlined />, label: '测试进行中' },
  { key: 'results', icon: <BarChartOutlined />, label: '最后统计结果' },
];

interface TestCaseView extends AgentCase {
  id: string;
  name: string;
  type: string;
  documents: string[];
  citations: Citation[];
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null
);

const stringValue = (value: unknown, fallback = '') => (
  value === undefined || value === null ? fallback : String(value)
);

const stringArray = (value: unknown): string[] => (
  Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
);

const citationArray = (value: unknown): Citation[] => (
  Array.isArray(value)
    ? value.filter(isRecord).map((item) => ({ ...item }))
    : []
);

const normalizeCase = (item: AgentCase): TestCaseView => ({
  ...item,
  id: stringValue(item.id ?? item.scenario_id),
  name: stringValue(item.name ?? item.scenario_name),
  type: stringValue(item.type ?? item.feature_id, '测试用例'),
  documents: stringArray(item.documents),
  citations: citationArray(item.citations),
});

const errorMessage = (error: unknown) => (
  error instanceof Error ? error.message : String(error)
);

const timestampValue = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
};

export default function App() {
  const [availableCases, setAvailableCases] = useState<TestCaseView[]>([]);
  const [currentKey, setCurrentKey] = useState('welcome');
  const [selectedCases, setSelectedCases] = useState<string[]>([]);
  const [isTesting, setIsTesting] = useState(false);
  const [logs, setLogs] = useState('');
  const [screenshots, setScreenshots] = useState<ScreenshotItem[]>([]);
  const [runStartedAt, setRunStartedAt] = useState<number | undefined>();
  const [currentImgIndex, setCurrentImgIndex] = useState(0);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [activeCase, setActiveCase] = useState<TestCaseView | null>(null);
  const [caseLoadError, setCaseLoadError] = useState('');
  const logOffsetRef = useRef(0);
  const logEndRef = useRef<HTMLPreElement>(null);
  const restoreTokenRef = useRef(0);
  const caseIds = availableCases.map((item) => item.id);
  const allCasesSelected = caseIds.length > 0 && selectedCases.length === caseIds.length;
  const partiallySelected = selectedCases.length > 0 && selectedCases.length < caseIds.length;
  const currentScreenshot = screenshots[currentImgIndex];

  const scrollLogsToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const logElement = logEndRef.current;
      if (logElement) {
        logElement.scrollTop = logElement.scrollHeight;
      }
    });
  }, []);

  const handleToggleAllCases = (checked: boolean) => {
    setSelectedCases(checked ? caseIds : []);
  };

  useEffect(() => {
    if (screenshots.length > 0) {
      setCurrentImgIndex(screenshots.length - 1);
    }
  }, [screenshots.length]);

  useEffect(() => {
    agentApi.getCases().then((res) => {
      const data = res.data;
      const cases = Array.isArray(data) ? data : data.cases;
      setAvailableCases(cases.map(normalizeCase).filter((item) => item.id));
      setCaseLoadError('');
    }).catch((error: unknown) => {
      setAvailableCases([]);
      setCaseLoadError(errorMessage(error));
    });
  }, []);

  useEffect(() => {
    let isActive = true;
    const restoreToken = restoreTokenRef.current;

    const restoreExecutionState = async () => {
      try {
        const [statusRes, logRes, imgRes] = await Promise.all([
          agentApi.getRunStatus(),
          agentApi.getLogs(0),
          agentApi.getScreenshots(),
        ]);

        if (!isActive || restoreToken !== restoreTokenRef.current) {
          return;
        }

        const restoredLogs = logRes.data?.content ?? '';
        const restoredOffset = typeof logRes.data?.offset === 'number'
          ? logRes.data.offset
          : restoredLogs.length;
        const restoredScreenshots = Array.isArray(imgRes.data?.items) ? imgRes.data.items : [];
        const restoredStatus = statusRes.data?.status;
        const restoredStartedAt = timestampValue(statusRes.data?.started_at);

        setRunStartedAt(restoredStartedAt);
        setLogs(restoredLogs);
        logOffsetRef.current = restoredOffset;
        setScreenshots(restoredScreenshots);
        if (restoredLogs) {
          scrollLogsToBottom();
        }

        const hasRestoredExecution = restoredLogs.trim().length > 0 || restoredScreenshots.length > 0;
        if (restoredStatus === 'running') {
          setIsTesting(true);
          setCurrentKey('ongoing');
          return;
        }

        setIsTesting(false);
        if (hasRestoredExecution) {
          setCurrentKey('ongoing');
        }
      } catch (error: unknown) {
        if (!isActive || restoreToken !== restoreTokenRef.current) {
          return;
        }
        setLogs((prev) => prev || `恢复执行状态失败: ${errorMessage(error)}`);
        logOffsetRef.current = 0;
      }
    };

    restoreExecutionState();

    return () => {
      isActive = false;
    };
  }, [scrollLogsToBottom]);

  const handleStartTest = async (mode: StartMode = 'resume') => {
    restoreTokenRef.current += 1;
    setCurrentKey('ongoing');
    setLogs('');
    setScreenshots([]);
    setCurrentImgIndex(0);
    setRunStartedAt(undefined);
    logOffsetRef.current = 0;
    try {
      const res = await agentApi.startTest({
        mode,
        cases: mode === 'resume' ? selectedCases : [],
      });
      setRunStartedAt(timestampValue(res.data?.started_at));
      setIsTesting(true);
    } catch (error: unknown) {
      setIsTesting(false);
      setLogs(`启动失败: ${errorMessage(error)}`);
    }
  };

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    let isPolling = false;
    let isActive = true;

    const pollExecutionState = async () => {
      if (isPolling) {
        return;
      }
      isPolling = true;
      try {
        const [logRes, imgRes, statusRes] = await Promise.all([
          agentApi.getLogs(logOffsetRef.current),
          agentApi.getScreenshots(runStartedAt),
          agentApi.getRunStatus(),
        ]);

        if (!isActive) {
          return;
        }

        if (logRes.data.content) {
          setLogs(prev => prev + logRes.data.content);
          logOffsetRef.current = logRes.data.offset;
          scrollLogsToBottom();
        }
        setScreenshots(Array.isArray(imgRes.data?.items) ? imgRes.data.items : []);
        if (['completed', 'failed'].includes(statusRes.data?.status)) {
          setIsTesting(false);
          setCurrentKey('results');
        }
      } catch (error: unknown) {
        if (!isActive) {
          return;
        }
        setIsTesting(false);
        setLogs(prev => `${prev}\n轮询失败: ${errorMessage(error)}`);
      } finally {
        isPolling = false;
      }
    };

    if (isTesting) {
      pollExecutionState();
      timer = setInterval(pollExecutionState, 1000);
    }
    return () => {
      isActive = false;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [isTesting, runStartedAt, scrollLogsToBottom]);

  const renderReferenceModal = () => {
    const documents = activeCase?.documents ?? [];
    const citations = activeCase?.citations ?? [];

    return (
      <Modal
        title={<span><ReadOutlined /> {activeCase?.name} - 参考文档</span>}
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setIsModalOpen(false)}>
            关闭
          </Button>,
        ]}
        width={720}
      >
        {documents.length > 0 && (
          <Space wrap style={{ marginBottom: 16 }}>
            {documents.map((document) => (
              <Tag key={document} color="blue">{document}</Tag>
            ))}
          </Space>
        )}
        <List
          dataSource={citations}
          locale={{ emptyText: '暂无引用来源' }}
          renderItem={(citation, index) => (
            <List.Item>
              <div style={{ width: '100%' }}>
                <Text strong>{citation.title || citation.citation_id || `引用 ${index + 1}`}</Text>
                {citation.source && (
                  <Paragraph style={{ marginTop: 8, marginBottom: 8 }}>
                    <Text type="secondary">来源: </Text>
                    <Text code>{citation.source}</Text>
                  </Paragraph>
                )}
                {citation.quote && (
                  <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                    {citation.quote}
                  </Paragraph>
                )}
              </div>
            </List.Item>
          )}
        />
      </Modal>
    );
  };

  const renderContent = () => {
    switch (currentKey) {
      case 'welcome':
        return (
          <div style={{ textAlign: 'center', paddingTop: '10%' }}>
            <Title>欢迎使用 Web Test Agent</Title>
            <Paragraph style={{ fontSize: 18 }}>您的自动化测试可视化控制中心</Paragraph>
            <Button type="primary" size="large" onClick={() => setCurrentKey('cases')}>进入用例管理</Button>
          </div>
        );
      case 'cases':
        return (
          <>
            <Card
              title="可用测试用例列表"
              extra={
                <Space>
                  <Button
                    icon={<PlayCircleOutlined />}
                    onClick={() => handleStartTest('full')}
                  >
                    生成并测试全部
                  </Button>
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    disabled={selectedCases.length === 0}
                    onClick={() => handleStartTest('resume')}
                  >
                    开始选定测试
                  </Button>
                </Space>
              }
            >
              <div style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 12, marginBottom: 4 }}>
                <Checkbox
                  checked={allCasesSelected}
                  indeterminate={partiallySelected}
                  disabled={availableCases.length === 0}
                  onChange={(event) => handleToggleAllCases(event.target.checked)}
                >
                  <Text strong>全选</Text>
                  <Text type="secondary" style={{ marginLeft: 8 }}>
                    已选择 {selectedCases.length} / {availableCases.length}
                  </Text>
                </Checkbox>
              </div>
              <Checkbox.Group
                style={{ width: '100%' }}
                value={selectedCases}
                onChange={(val) => setSelectedCases(val.map(String))}
              >
                <List
                  rowKey={(item) => item.id}
                  dataSource={availableCases}
                  locale={{
                    emptyText: caseLoadError
                      ? `加载现有测试用例失败: ${caseLoadError}`
                      : '暂无现有测试用例，可点击“生成并测试全部”创建新的用例',
                  }}
                  renderItem={(item) => (
                    <List.Item
                      actions={[
                        <Button
                          type="link"
                          icon={<ReadOutlined />}
                          onClick={() => {
                            setActiveCase(item);
                            setIsModalOpen(true);
                          }}
                        >
                          查看参考
                        </Button>,
                      ]}
                    >
                      <Checkbox value={item.id}>
                        <Text strong>{item.name}</Text> <Tag color="blue" style={{ marginLeft: 8 }}>{item.type}</Tag>
                      </Checkbox>
                    </List.Item>
                  )}
                />
              </Checkbox.Group>
            </Card>
            {renderReferenceModal()}
          </>
        );
      case 'ongoing':
        return (
          <Row gutter={24} style={{ height: 'calc(100vh - 140px)' }}>
            <Col span={14} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <Title level={4}>实时屏幕监控</Title>
              <div style={{
                flex: 1,
                background: '#fafafa',
                border: '1px solid #d9d9d9',
                borderRadius: 8,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                alignItems: 'center',
                padding: 16,
                overflow: 'hidden',
              }}>
                {screenshots.length === 0 || !currentScreenshot ? (
                  <Empty description="等待 Agent 捕获首张截图..." />
                ) : (
                  <>
                    <Space wrap style={{ width: '100%', marginBottom: 12 }}>
                      <Tag color="blue">
                        用例: {currentScreenshot?.scenario_id || '未识别'}
                      </Tag>
                      {currentScreenshot?.scenario_name && (
                        <Text strong>{currentScreenshot.scenario_name}</Text>
                      )}
                      {currentScreenshot?.step !== null && currentScreenshot?.step !== undefined && (
                        <Tag>步骤 {currentScreenshot.step}</Tag>
                      )}
                      {currentScreenshot?.status && (
                        <Tag color={currentScreenshot.status === '失败' ? 'red' : 'green'}>
                          {currentScreenshot.status}
                        </Tag>
                      )}
                    </Space>
                    <div style={{ flex: 1, width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
                      <img
                        src={getImageUrl(currentScreenshot?.url || '')}
                        alt="agent-screenshot"
                        style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 4, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
                      />
                    </div>
                    <Space style={{ marginTop: 20 }}>
                      <Button
                        shape="circle"
                        icon={<LeftOutlined />}
                        onClick={() => setCurrentImgIndex(prev => Math.max(0, prev - 1))}
                        disabled={currentImgIndex === 0}
                      />
                      <Text strong style={{ fontSize: 16, margin: '0 10px' }}>
                        {currentImgIndex + 1} / {screenshots.length}
                      </Text>
                      <Button
                        shape="circle"
                        icon={<RightOutlined />}
                        onClick={() => setCurrentImgIndex(prev => Math.min(screenshots.length - 1, prev + 1))}
                        disabled={currentImgIndex === screenshots.length - 1}
                      />
                    </Space>
                  </>
                )}
              </div>
            </Col>
            <Col span={10} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <Title level={4}>
                执行日志 {isTesting && <LoadingOutlined style={{ color: '#1890ff', marginLeft: 8 }} />}
              </Title>
              <pre
                ref={logEndRef}
                style={{
                  flex: 1,
                  background: '#1e1e1e',
                  color: '#00ff00',
                  padding: '16px',
                  borderRadius: 8,
                  overflowY: 'auto',
                  margin: 0,
                  fontFamily: 'Consolas, monospace',
                  fontSize: 14,
                  lineHeight: 1.5,
                  boxShadow: 'inset 0 0 10px rgba(0,0,0,0.5)',
                }}
              >
                {logs || '等待初始化...'}
              </pre>
            </Col>
          </Row>
        );
      case 'results':
        return (
          <Row gutter={16}>
            <Col span={8}><Card><Statistic title="总计用例" value={selectedCases.length || 0} /></Card></Col>
            <Col span={8}><Card><Statistic title="通过率" value={100} suffix="%" valueStyle={{ color: '#3f8600' }} /></Card></Col>
            <Col span={8}><Card><Statistic title="平均耗时" value={4.2} suffix="s" /></Card></Col>
          </Row>
        );
      default:
        return null;
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible theme="dark">
        <div style={{ height: 32, margin: 16, background: 'rgba(255, 255, 255, 0.2)', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>AGENT UI</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[currentKey]}
          onClick={({ key }) => setCurrentKey(String(key))}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Content style={{ margin: '24px', padding: '24px', background: '#fff', borderRadius: 8, overflow: 'initial' }}>
          {renderContent()}
        </Content>
      </Layout>
    </Layout>
  );
}
