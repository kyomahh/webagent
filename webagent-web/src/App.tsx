import { useState, useEffect, useRef, useCallback } from 'react';
import { Layout, Menu, Typography, Button, Card, Row, Col, List, Statistic, Empty, Tag, Space, Modal, Table, Pagination, Alert, Progress, Form, Input } from 'antd';
import {
  HomeOutlined,
  UnorderedListOutlined,
  LoadingOutlined,
  BarChartOutlined,
  PlayCircleOutlined,
  LeftOutlined,
  RightOutlined,
  ReadOutlined,
  RobotOutlined,
  EyeOutlined,
  CodeOutlined,
  FileProtectOutlined,
  UserOutlined,
  LockOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons';
import { AUTH_TOKEN_KEY, AUTH_USER_KEY, agentApi, getImageUrl, type AgentCase, type AuthUser, type Citation, type ScreenshotItem, type StartMode, type SummaryResponse } from './api/agent';
import './App.css';

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

const errorMessage = (error: unknown) => {
  if (isRecord(error)) {
    const response = error.response;
    if (isRecord(response)) {
      const data = response.data;
      if (isRecord(data) && data.detail) {
        return String(data.detail);
      }
      if (typeof data === 'string' && data) {
        return data;
      }
    }
  }
  return error instanceof Error ? error.message : String(error);
};

const statusLabel = (status?: string) => {
  switch (status) {
    case 'running':
      return '测试运行中';
    case 'completed':
      return '测试已结束';
    case 'failed':
      return '测试进程失败';
    case 'starting':
      return '正在启动';
    case 'idle':
      return '空闲';
    default:
      return '等待状态';
  }
};

const statusTagColor = (status?: string) => {
  switch (status) {
    case 'running':
    case 'starting':
      return 'processing';
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    default:
      return 'default';
  }
};

const resultStatusLabel = (status?: string, passed?: boolean) => {
  if (status === 'ignored') {
    return '可忽略';
  }
  if (status === 'passed' || passed) {
    return '通过';
  }
  if (status === 'failed' || passed === false) {
    return '失败';
  }
  return '未验证';
};

const resultStatusColor = (status?: string, passed?: boolean) => {
  if (status === 'ignored') {
    return 'default';
  }
  if (status === 'passed' || passed) {
    return 'green';
  }
  if (status === 'failed' || passed === false) {
    return 'red';
  }
  return 'blue';
};

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

const isAuthUser = (value: unknown): value is AuthUser => (
  isRecord(value)
  && typeof value.username === 'string'
  && (value.role === 'admin' || value.role === 'user')
);

const readStoredUser = (): AuthUser | null => {
  const rawUser = localStorage.getItem(AUTH_USER_KEY);
  if (!rawUser) {
    return null;
  }
  try {
    const parsed = JSON.parse(rawUser);
    return isAuthUser(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

const clearAuthStorage = () => {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
};

const roleLabel = (role?: string) => (
  role === 'admin' ? '管理员' : '普通用户'
);

export default function App() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [availableCases, setAvailableCases] = useState<TestCaseView[]>([]);
  const [currentKey, setCurrentKey] = useState('welcome');
  const [selectedCases, setSelectedCases] = useState<string[]>([]);
  const [isTesting, setIsTesting] = useState(false);
  const [logs, setLogs] = useState('');
  const [screenshots, setScreenshots] = useState<ScreenshotItem[]>([]);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [runStatus, setRunStatus] = useState('idle');
  const [runMessage, setRunMessage] = useState('');
  const [runStartedAt, setRunStartedAt] = useState<number | undefined>();
  const [currentImgIndex, setCurrentImgIndex] = useState(0);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [activeCase, setActiveCase] = useState<TestCaseView | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [isOnline, setIsOnline] = useState(false);
  const [caseLoadError, setCaseLoadError] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const logOffsetRef = useRef(0);
  const logEndRef = useRef<HTMLPreElement>(null);
  const restoreTokenRef = useRef(0);
  const isAdmin = currentUser?.role === 'admin';
  const caseIds = availableCases.map((item) => item.id);
  const isAllSelected = selectedCases.length === availableCases.length && availableCases.length > 0;
  const currentScreenshot = screenshots[currentImgIndex];
  const verification = summary?.verification;
  const verificationItems = verification?.items ?? [];
  const selectedResultItems = selectedCases.length > 0
    ? verificationItems.filter((item) => selectedCases.includes(item.scenario_id))
    : verificationItems;
  const resultItems = selectedResultItems.length > 0 || selectedCases.length === 0
    ? selectedResultItems
    : verificationItems;
  const resultPassedCount = resultItems.filter((item) => item.status !== 'ignored' && (item.status === 'passed' || item.passed)).length;
  const resultFailedCount = resultItems.filter((item) => item.status === 'failed' || (item.status !== 'ignored' && item.passed === false)).length;
  const resultIgnoredCount = resultItems.filter((item) => item.status === 'ignored').length;
  const resultVerifiedCount = resultItems.length;
  const firstFailedResult = resultItems.find((item) => item.status === 'failed' || (item.status !== 'ignored' && item.passed === false));
  const displayExpectedCount = selectedCases.length || verification?.expected_count || resultVerifiedCount;
  const effectiveResultTotal = resultPassedCount + resultFailedCount;
  const displayPassRate = effectiveResultTotal > 0
    ? Math.round((resultPassedCount / effectiveResultTotal) * 1000) / 10
    : null;
  const liveVerifiedCount = selectedCases.length > 0
    ? selectedResultItems.length
    : verification?.verified_count ?? verificationItems.length;
  const liveExpectedCount = selectedCases.length || verification?.expected_count || liveVerifiedCount;
  const liveProgressPercent = liveExpectedCount > 0
    ? Math.min(100, Math.round((Math.min(liveVerifiedCount, liveExpectedCount) / liveExpectedCount) * 100))
    : 0;

  const scrollLogsToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const logElement = logEndRef.current;
      if (logElement) {
        logElement.scrollTop = logElement.scrollHeight;
      }
    });
  }, []);

  useEffect(() => {
    let isActive = true;
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (!token) {
      clearAuthStorage();
      setIsAuthReady(true);
      return;
    }

    const storedUser = readStoredUser();
    if (storedUser) {
      setCurrentUser(storedUser);
    }

    agentApi.me().then((res) => {
      if (!isActive) {
        return;
      }
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(res.data.user));
      setCurrentUser(res.data.user);
      setLoginError('');
    }).catch(() => {
      if (!isActive) {
        return;
      }
      clearAuthStorage();
      setCurrentUser(null);
      setLoginError('登录已过期，请重新登录');
    }).finally(() => {
      if (isActive) {
        setIsAuthReady(true);
      }
    });

    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    const handleAuthExpired = () => {
      clearAuthStorage();
      setCurrentUser(null);
      setIsTesting(false);
      setIsOnline(false);
      setLoginError('登录已过期，请重新登录');
    };

    window.addEventListener('webagent-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('webagent-auth-expired', handleAuthExpired);
  }, []);

  const handleLogin = async (values: { username: string; password: string }) => {
    setIsLoggingIn(true);
    setLoginError('');
    try {
      const res = await agentApi.login(values);
      localStorage.setItem(AUTH_TOKEN_KEY, res.data.access_token);
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(res.data.user));
      setCurrentUser(res.data.user);
      setCurrentKey('welcome');
      setIsOnline(false);
    } catch (error: unknown) {
      clearAuthStorage();
      setCurrentUser(null);
      setLoginError(errorMessage(error));
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = () => {
    clearAuthStorage();
    setCurrentUser(null);
    setIsTesting(false);
    setIsOnline(false);
    setSelectedCases([]);
    setLogs('');
    setScreenshots([]);
    setSummary(null);
    setRunStatus('idle');
    setCurrentKey('welcome');
  };

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(availableCases.length / pageSize));
    if (currentPage > maxPage) {
      setCurrentPage(1);
    }
  }, [availableCases.length, pageSize, currentPage]);

  useEffect(() => {
    if (screenshots.length > 0) {
      setCurrentImgIndex(screenshots.length - 1);
    }
  }, [screenshots.length]);

  useEffect(() => {
    if (!currentUser) {
      return;
    }

    const fetchCases = () => {
      agentApi.getCases().then((res) => {
        const data = res.data;
        const cases = Array.isArray(data)
          ? data
          : Array.isArray(data?.cases)
            ? data.cases
            : [];
        setAvailableCases(cases.map(normalizeCase).filter((item) => item.id));
        setCaseLoadError('');
        setIsOnline(true);
      }).catch((error: unknown) => {
        setAvailableCases([]);
        setCaseLoadError(errorMessage(error));
        setIsOnline(false);
      });
    };

    fetchCases();

    const interval = setInterval(fetchCases, 5000);
    return () => clearInterval(interval);
  }, [currentUser]);

  useEffect(() => {
    if (!currentUser) {
      return;
    }

    let isActive = true;
    const restoreToken = restoreTokenRef.current;

    const restoreExecutionState = async () => {
      try {
        const [statusRes, logRes, imgRes, summaryRes] = await Promise.all([
          agentApi.getRunStatus(),
          agentApi.getLogs(0),
          agentApi.getScreenshots(),
          agentApi.getSummary(),
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
        setRunStatus(restoredStatus || 'idle');
        setSummary(summaryRes.data);
        setLogs(restoredLogs);
        logOffsetRef.current = restoredOffset;
        setScreenshots(restoredScreenshots);
        if (restoredLogs) {
          scrollLogsToBottom();
        }

        const hasRestoredExecution = restoredLogs.trim().length > 0 || restoredScreenshots.length > 0;
        if (restoredStatus === 'running') {
          setIsTesting(true);
          setIsOnline(true);
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
  }, [scrollLogsToBottom, currentUser]);

  const handleStartTest = async (mode: StartMode = 'resume') => {
    if (!isAdmin) {
      setRunMessage('当前账号没有启动测试权限');
      return;
    }
    restoreTokenRef.current += 1;
    setCurrentKey('ongoing');
    setLogs('');
    setScreenshots([]);
    setSummary(null);
    setRunStatus('starting');
    setRunMessage('');
    setCurrentImgIndex(0);
    setRunStartedAt(undefined);
    logOffsetRef.current = 0;
    try {
      const res = await agentApi.startTest({
        mode,
        cases: mode === 'resume' ? selectedCases : [],
      });
      setRunStartedAt(timestampValue(res.data?.started_at));
      const status = String(res.data?.status || 'running');
      setRunStatus(status === 'success' ? 'running' : status);
      setRunMessage(String(res.data?.message || ''));
      setIsTesting(true);
      setIsOnline(true);
    } catch (error: unknown) {
      setIsTesting(false);
      setRunStatus('failed');
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
        const [logRes, imgRes, statusRes, summaryRes] = await Promise.all([
          agentApi.getLogs(logOffsetRef.current),
          agentApi.getScreenshots(runStartedAt),
          agentApi.getRunStatus(),
          agentApi.getSummary(),
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
        setSummary(summaryRes.data);
        setRunStatus(statusRes.data?.status || 'idle');
        if (['completed', 'failed'].includes(statusRes.data?.status)) {
          setIsTesting(false);
          setCurrentKey('results');
        }
      } catch (error: unknown) {
        if (!isActive) {
          return;
        }
        setIsTesting(false);
        setRunStatus('failed');
        setLogs(prev => `${prev}\n轮询失败: ${errorMessage(error)}`);
      } finally {
        isPolling = false;
      }
    };

    if (isTesting && currentUser) {
      pollExecutionState();
      timer = setInterval(pollExecutionState, 1000);
    }
    return () => {
      isActive = false;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [isTesting, runStartedAt, scrollLogsToBottom, currentUser]);

  useEffect(() => {
    let isActive = true;

    const refreshFinalSummary = async () => {
      if (currentKey !== 'results' || !currentUser) {
        return;
      }
      try {
        const [statusRes, summaryRes] = await Promise.all([
          agentApi.getRunStatus(),
          agentApi.getSummary(),
        ]);
        if (!isActive) {
          return;
        }
        setRunStatus(statusRes.data?.status || 'idle');
        setSummary(summaryRes.data);
      } catch (error: unknown) {
        if (!isActive) {
          return;
        }
        setRunMessage(`刷新结果失败: ${errorMessage(error)}`);
      }
    };

    refreshFinalSummary();

    return () => {
      isActive = false;
    };
  }, [currentKey, currentUser]);

  const renderLogin = () => (
    <div className="login-page">
      <Card className="login-card" bordered={false}>
        <div className="login-header">
          <div className="login-icon">
            <SafetyCertificateOutlined />
          </div>
          <Title level={3} style={{ marginBottom: 4 }}>Web Test Agent</Title>
          <Text type="secondary">登录控制台</Text>
        </div>

        {loginError && (
          <Alert
            type="error"
            showIcon
            message={loginError}
            style={{ marginBottom: 16 }}
          />
        )}

        <Form
          layout="vertical"
          onFinish={handleLogin}
          autoComplete="off"
          initialValues={{ username: 'admin' }}
        >
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="admin"
              size="large"
            />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入密码"
              size="large"
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={isLoggingIn}
            size="large"
            block
          >
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );

  const renderReferenceModal = () => {
    const documents = activeCase?.documents ?? [];
    const citations = activeCase?.citations ?? [];
    const hasStructuredReferences = documents.length > 0 || citations.length > 0;

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
        {hasStructuredReferences ? (
          <>
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
              renderItem={(citation: Citation, index: number) => (
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
          </>
        ) : (
          <div style={{ marginTop: 16 }}>
            <Paragraph>
              <Text type="secondary">文档路径: </Text>
              <Text code>/agent_workspace/docs/case_{activeCase?.id}.md</Text>
            </Paragraph>
            <Paragraph>
              <Text type="secondary">生成来源: </Text>
              Agent 根据 PRD 自动抽取并分析生成。
            </Paragraph>

            <div style={{
              background: '#f5f5f5',
              padding: '16px',
              borderRadius: '8px',
              border: '1px solid #e8e8e8',
              marginTop: '16px'
            }}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'Consolas, monospace', color: '#333' }}>
                {`# 测试目的
验证【${activeCase?.name || '当前功能'}】的核心链路是否符合预期，确保系统稳定性。

# 前置条件
1. Agent 已成功获取 DOM 树结构。
2. 测试账号状态正常且已登录。
3. 网络环境无严重延迟。

# 预期结果
- 页面渲染无报错日志 (Console clean)。
- 关键交互节点截图与基准 UI 匹配度 > 95%。
- 业务流转符合设定逻辑。`}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    );
  };

  const renderContent = () => {
    switch (currentKey) {
      case 'welcome':
        return (
          <div className="welcome-container animate-fade-in">
            <Title className="gradient-title">Web Test Agent</Title>
            <Paragraph className="welcome-subtitle">
              基于大模型的智能化 Web 自动化测试与实时可视化监控控制台
            </Paragraph>

            <div className="welcome-stats">
              <Row gutter={16}>
                <Col span={8}>
                  <div className="welcome-stat-item">
                    <Statistic
                      title="已配置可用测试用例"
                      value={availableCases.length}
                      valueStyle={{ color: 'var(--primary-color)', fontWeight: 700 }}
                    />
                  </div>
                </Col>
                <Col span={8}>
                  <div className="welcome-stat-item">
                    <Statistic
                      title="系统连接状态"
                      value={isOnline ? 'Online' : 'Offline'}
                      valueStyle={{ color: isOnline ? 'var(--success-color)' : '#ef4444', fontWeight: 700 }}
                    />
                  </div>
                </Col>
                <Col span={8}>
                  <div className="welcome-stat-item">
                    <Statistic
                      title="Agent 运行环境"
                      value="Local API"
                      valueStyle={{ color: 'var(--warning-color)', fontWeight: 700 }}
                    />
                  </div>
                </Col>
              </Row>
            </div>

            <div className="features-grid">
              <Row gutter={[20, 20]}>
                <Col xs={24} sm={12} md={6}>
                  <Card className="feature-card" bordered={false}>
                    <div className="feature-icon-wrapper">
                      <RobotOutlined />
                    </div>
                    <Title level={5} className="feature-card-title">智能 DOM 分析</Title>
                    <Paragraph className="feature-card-description">
                      大模型自动解析页面元素树，提取关键操作节点，无需手动编写定位选择器。
                    </Paragraph>
                  </Card>
                </Col>
                <Col xs={24} sm={12} md={6}>
                  <Card className="feature-card" bordered={false}>
                    <div className="feature-icon-wrapper">
                      <EyeOutlined />
                    </div>
                    <Title level={5} className="feature-card-title">实时屏幕监控</Title>
                    <Paragraph className="feature-card-description">
                      高频捕获测试界面截图，结合轮播回放历史，让 Agent 的每一步操作尽在掌控。
                    </Paragraph>
                  </Card>
                </Col>
                <Col xs={24} sm={12} md={6}>
                  <Card className="feature-card" bordered={false}>
                    <div className="feature-icon-wrapper">
                      <CodeOutlined />
                    </div>
                    <Title level={5} className="feature-card-title">实时的终端日志</Title>
                    <Paragraph className="feature-card-description">
                      流式加载后端测试任务的系统日志，实时显示每步决策与执行状态的演进。
                    </Paragraph>
                  </Card>
                </Col>
                <Col xs={24} sm={12} md={6}>
                  <Card className="feature-card" bordered={false}>
                    <div className="feature-icon-wrapper">
                      <FileProtectOutlined />
                    </div>
                    <Title level={5} className="feature-card-title">需求参考比对</Title>
                    <Paragraph className="feature-card-description">
                      自动抽取 PRD 及需求规格文档为参考指标，自动化校验期望结果是否达成。
                    </Paragraph>
                  </Card>
                </Col>
              </Row>
            </div>

            <div className="cta-section">
              <Paragraph style={{ fontSize: 16, marginBottom: 20, color: 'var(--text-main)', fontWeight: 500 }}>
                {isAdmin
                  ? '准备好验证您的系统了吗？选择需要测试的功能用例，立即启动 AI 自动化测试。'
                  : '当前账号为只读用户，可查看测试用例、执行过程和统计结果。'}
              </Paragraph>
              <Button
                type="primary"
                size="large"
                className="cta-button"
                onClick={() => setCurrentKey('cases')}
              >
                {isAdmin ? '进入用例管理并开始' : '查看测试用例'}
              </Button>
            </div>
          </div>
        );
      case 'cases': {
        const columns = [
          {
            title: '用例 ID / 编号',
            dataIndex: 'id',
            key: 'id',
            width: 160,
            render: (text: string) => <Text code>{text}</Text>,
          },
          {
            title: '测试用例名称',
            dataIndex: 'name',
            key: 'name',
            render: (text: string) => <Text strong>{text}</Text>,
          },
          {
            title: '所属分类',
            dataIndex: 'type',
            key: 'type',
            width: 180,
            render: (text: string) => <Tag color="blue">{text}</Tag>,
          },
          {
            title: '操作',
            key: 'action',
            width: 140,
            align: 'center' as const,
            render: (_: unknown, record: TestCaseView) => (
              <Button
                type="link"
                icon={<ReadOutlined />}
                onClick={(event) => {
                  event.stopPropagation();
                  setActiveCase(record);
                  setIsModalOpen(true);
                }}
              >
                查看参考
              </Button>
            ),
          },
        ];

        const paginatedCases = availableCases.slice(
          (currentPage - 1) * pageSize,
          currentPage * pageSize
        );

        return (
          <div className="cases-page-container">
            <div className="cases-header-row">
              <span className="cases-title">可用测试用例列表</span>
              <Space>
                {isAdmin ? (
                  <>
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
                      测试选中用例
                    </Button>
                    {availableCases.length > 0 && (
                      <Button
                        type={isAllSelected ? 'default' : 'primary'}
                        ghost={!isAllSelected}
                        onClick={() => {
                          if (isAllSelected) {
                            setSelectedCases([]);
                          } else {
                            setSelectedCases(caseIds);
                          }
                        }}
                        style={{ borderRadius: '6px' }}
                      >
                        {isAllSelected ? '清空选择' : `选择所有用例 (${availableCases.length})`}
                      </Button>
                    )}
                  </>
                ) : (
                  <Tag color="blue">只读模式</Tag>
                )}
              </Space>
            </div>

            <div className="table-wrapper">
              <Table
                rowKey="id"
                dataSource={paginatedCases}
                columns={columns}
                pagination={false}
                rowSelection={isAdmin ? {
                  selectedRowKeys: selectedCases,
                  onChange: (keys) => setSelectedCases(keys.map(String)),
                } : undefined}
                onRow={(record) => ({
                  onClick: () => {
                    if (!isAdmin) {
                      return;
                    }
                    const key = record.id;
                    const selected = selectedCases.includes(key);
                    if (selected) {
                      setSelectedCases(prev => prev.filter(item => item !== key));
                    } else {
                      setSelectedCases(prev => [...prev, key]);
                    }
                  },
                  style: { cursor: 'pointer' },
                })}
                locale={{
                  emptyText: (
                    <Empty
                      description={
                        caseLoadError
                          ? `加载现有测试用例失败: ${caseLoadError}`
                          : '暂无现有测试用例，可点击“生成并测试全部”创建新的用例'
                      }
                    />
                  ),
                }}
              />
            </div>

            {availableCases.length > 0 && (
              <div className="pagination-wrapper">
                <Pagination
                  current={currentPage}
                  pageSize={pageSize}
                  total={availableCases.length}
                  onChange={(page, size) => {
                    setCurrentPage(page);
                    setPageSize(size);
                  }}
                  showSizeChanger
                  showTotal={(total) => `共 ${total} 条用例`}
                />
              </div>
            )}

            <div className={`floating-action-bar ${isAdmin && selectedCases.length > 0 ? 'visible' : ''}`}>
              <span className="floating-action-info">
                已选择 <strong style={{ color: '#2563eb', fontSize: 16 }}>{selectedCases.length}</strong> / {availableCases.length} 项测试用例
              </span>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                className="floating-action-btn"
                onClick={() => handleStartTest('resume')}
              >
                开始选定测试
              </Button>
            </div>

            {renderReferenceModal()}
          </div>
        );
      }
      case 'ongoing':
        return (
          <div className="execution-page">
            <div className="execution-status-panel">
              <Space wrap align="center">
                <Tag color={statusTagColor(runStatus)}>{statusLabel(runStatus)}</Tag>
                {isTesting && <LoadingOutlined style={{ color: '#2563eb' }} />}
                <Text strong>
                  已验证 {liveVerifiedCount} / {liveExpectedCount || '待确认'} 个用例
                </Text>
                {runMessage && <Text type="secondary">{runMessage}</Text>}
              </Space>
              <Progress
                percent={liveProgressPercent}
                status={runStatus === 'failed' ? 'exception' : isTesting ? 'active' : 'success'}
                strokeColor={runStatus === 'failed' ? '#ef4444' : '#2563eb'}
              />
              <Space wrap>
                <Tag color="green">通过 {verification?.passed_count ?? resultPassedCount}</Tag>
                <Tag color="red">失败 {verification?.failed_count ?? resultFailedCount}</Tag>
                <Tag>可忽略 {verification?.ignored_count ?? resultIgnoredCount}</Tag>
                <Tag>截图 {summary?.screenshots?.count ?? screenshots.length}</Tag>
              </Space>
            </div>

            <Row gutter={24} className="execution-main-row">
              <Col span={14} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                <Title level={4}>实时屏幕监控</Title>

                <div className="screenshot-panel">
                  {screenshots.length === 0 || !currentScreenshot ? (
                    <Empty description="等待 Agent 捕获首张截图..." />
                  ) : (
                    <>
                      <Space wrap style={{ width: '100%', marginBottom: 12 }}>
                        <Tag color="blue">
                          用例: {currentScreenshot.scenario_id || '未识别'}
                        </Tag>
                        {currentScreenshot.scenario_name && (
                          <Text strong>{currentScreenshot.scenario_name}</Text>
                        )}
                        {currentScreenshot.step !== null && currentScreenshot.step !== undefined && (
                          <Tag>步骤 {currentScreenshot.step}</Tag>
                        )}
                        {currentScreenshot.status && (
                          <Tag color={currentScreenshot.status === '失败' ? 'red' : 'green'}>
                            {currentScreenshot.status}
                          </Tag>
                        )}
                      </Space>
                      <div className="screenshot-stage">
                        <img
                          src={getImageUrl(currentScreenshot.url)}
                          alt="agent-screenshot"
                          className="screenshot-image"
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
                <pre ref={logEndRef} className="execution-log">
                  {logs || '等待初始化...'}
                </pre>
              </Col>
            </Row>
          </div>
        );
      case 'results':
        return (
          <div className="results-page">
            <div className="results-header">
              <div>
                <Title level={3} style={{ marginBottom: 8 }}>最后统计结果</Title>
                <Space wrap>
                  <Tag color={statusTagColor(runStatus)}>{statusLabel(runStatus)}</Tag>
                  <Text type="secondary">
                    测试库共 {summary?.test_cases?.count ?? availableCases.length} 条用例
                  </Text>
                  {summary?.latest_report?.path && (
                    <Text type="secondary">报告: <Text code>{summary.latest_report.path}</Text></Text>
                  )}
                </Space>
              </div>
              <Space>
                <Button onClick={() => setCurrentKey('ongoing')}>查看执行过程</Button>
                <Button type="primary" onClick={() => setCurrentKey('cases')}>返回用例列表</Button>
              </Space>
            </div>

            {runStatus === 'failed' && (
              <Alert
                type="error"
                showIcon
                message="测试进程异常结束"
                description="后端执行进程返回失败状态，请查看执行日志中的最后几行定位原因。"
              />
            )}

            {summary && resultVerifiedCount > 0 && runStatus !== 'failed' && (
              <Alert
                type={resultFailedCount > 0 ? 'error' : 'success'}
                showIcon
                message={resultFailedCount > 0 ? '验证失败' : '验证通过'}
                description={
                  resultFailedCount > 0
                    ? firstFailedResult?.reason || '存在未通过用例，请查看下方结果明细。'
                    : `已验证 ${resultVerifiedCount} 个用例，未发现有效失败。`
                }
              />
            )}

            {!summary && (
              <Alert
                type="info"
                showIcon
                message="暂无结果汇总"
                description="还没有获取到后端验证结果。测试结束后这里会显示通过率、失败数量和失败原因。"
              />
            )}

            <Row gutter={16}>
              <Col span={6}>
                <Card>
                  <Statistic title="本次已验证" value={resultVerifiedCount} suffix={`/ ${displayExpectedCount || 0}`} />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic title="通过" value={resultPassedCount} valueStyle={{ color: '#10b981' }} />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic title="失败" value={resultFailedCount} valueStyle={{ color: '#ef4444' }} />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic
                    title="通过率"
                    value={displayPassRate ?? 0}
                    suffix="%"
                    valueStyle={{ color: resultFailedCount > 0 ? '#ef4444' : '#10b981' }}
                  />
                </Card>
              </Col>
            </Row>

            <div className="results-table-wrap">
              <Table
                rowKey="scenario_id"
                dataSource={resultItems}
                pagination={{ pageSize: 8, showSizeChanger: false }}
                locale={{ emptyText: <Empty description="暂无验证结果，测试完成后会显示通过/失败原因" /> }}
                columns={[
                  {
                    title: '用例 ID',
                    dataIndex: 'scenario_id',
                    key: 'scenario_id',
                    width: 150,
                    render: (text: string) => <Text code>{text}</Text>,
                  },
                  {
                    title: '测试用例',
                    dataIndex: 'scenario_name',
                    key: 'scenario_name',
                    width: 260,
                    render: (text: string) => <Text strong>{text}</Text>,
                  },
                  {
                    title: '结果',
                    dataIndex: 'status',
                    key: 'status',
                    width: 120,
                    render: (status: string, record) => (
                      <Tag color={resultStatusColor(status, record.passed)}>
                        {resultStatusLabel(status, record.passed)}
                      </Tag>
                    ),
                  },
                  {
                    title: '原因',
                    dataIndex: 'reason',
                    key: 'reason',
                    render: (text: string) => (
                      <Text type={text ? undefined : 'secondary'}>
                        {text || '暂无原因'}
                      </Text>
                    ),
                  },
                ]}
              />
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  if (!isAuthReady) {
    return (
      <div className="login-page">
        <Space direction="vertical" align="center">
          <LoadingOutlined style={{ fontSize: 28, color: '#2563eb' }} />
          <Text type="secondary">正在检查登录状态...</Text>
        </Space>
      </div>
    );
  }

  if (!currentUser) {
    return renderLogin();
  }

  return (
    <Layout style={{ minHeight: '100vh', height: '100vh', overflow: 'hidden' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={(value) => setCollapsed(value)}
        className="modern-sider"
        width={240}
      >
        <div className="logo-container">
          {collapsed ? (
            <div className="logo-text" style={{ fontSize: 20 }}>AI</div>
          ) : (
            <>
              <div className="logo-text">WEBTEST // AGENT</div>
              <div className="logo-subtext">Control Panel</div>
            </>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[currentKey]}
          onClick={({ key }) => setCurrentKey(String(key))}
          items={menuItems}
        />
        {!collapsed && (
          <div className="sider-footer">
            {currentUser && (
              <div className="user-panel">
                <div className="user-meta">
                  <UserOutlined />
                  <span>{currentUser.display_name || currentUser.username}</span>
                  <Tag color={isAdmin ? 'gold' : 'blue'}>{roleLabel(currentUser.role)}</Tag>
                </div>
                <Button
                  size="small"
                  icon={<LogoutOutlined />}
                  onClick={handleLogout}
                  block
                >
                  退出登录
                </Button>
              </div>
            )}
            <div className="status-row">
              <span className={`status-dot ${isOnline ? 'online' : 'offline'}`}></span>
              <span>Agent Status: {isOnline ? 'Online' : 'Offline'}</span>
            </div>
            <div className="status-env">API: http://localhost:8000</div>
          </div>
        )}
      </Sider>
      <Layout style={{ background: '#f8fafc', height: '100vh', overflow: 'hidden' }}>
        <Content style={{
          margin: '24px',
          padding: '32px',
          background: '#ffffff',
          borderRadius: '16px',
          border: '1px solid #f1f5f9',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.02)',
          display: 'flex',
          flexDirection: 'column',
          height: 'calc(100vh - 48px)',
          overflow: 'hidden',
        }}>
          {renderContent()}
        </Content>
      </Layout>
    </Layout>
  );
}
