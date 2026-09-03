import React, { useState, useRef, useEffect } from 'react';
import { ChatMessage, Citation, ChatResponse } from '../types';

interface AskChatProps {
  isHealthy: boolean;
  onSwitchToCheckInvoice: () => void;
}

const MAX_CHARS = 2000;

export const AskChat: React.FC<AskChatProps> = ({
  isHealthy,
  onSwitchToCheckInvoice,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [quotaExhaustedError, setQuotaExhaustedError] = useState<{
    error: string;
    resets_at?: string;
  } | null>(null);
  const [rateLimitError, setRateLimitError] = useState<string | null>(null);

  const threadIdRef = useRef<string | null>(null);
  const lastSubmittedRef = useRef<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, networkError, quotaExhaustedError, rateLimitError]);

  const handleResetSession = () => {
    setMessages([]);
    threadIdRef.current = null;
    setInput('');
    setNetworkError(null);
    setQuotaExhaustedError(null);
    setRateLimitError(null);
    textareaRef.current?.focus();
  };

  const submitQuestion = async (questionText: string) => {
    const trimmed = questionText.trim();
    if (!trimmed || loading || !isHealthy) return;

    if (trimmed.length > MAX_CHARS) {
      return;
    }

    lastSubmittedRef.current = trimmed;
    setNetworkError(null);
    setQuotaExhaustedError(null);
    setRateLimitError(null);

    const userMessage: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: trimmed,
          thread_id: threadIdRef.current || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        if (response.status === 429) {
          if (data.resets_at) {
            setQuotaExhaustedError({
              error: data.error || 'The daily question quota is used up.',
              resets_at: data.resets_at,
            });
          } else {
            setRateLimitError('Slow down, try again in a minute.');
          }
        } else if (response.status === 413) {
          setNetworkError(data.error || `Message exceeds limit of ${MAX_CHARS} characters.`);
        } else {
          setNetworkError(data.error || 'Something went wrong handling that request.');
        }

        setInput(trimmed);
        setLoading(false);
        return;
      }

      const chatData = data as ChatResponse;
      if (chatData.thread_id) {
        threadIdRef.current = chatData.thread_id;
      }

      const assistantMessage: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: chatData.answer,
        route: chatData.route,
        citations: chatData.citations || [],
        tokens: chatData.tokens,
        models: chatData.models,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      setNetworkError('Could not reach the server. Please check your connection.');
      setInput(trimmed);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submitQuestion(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitQuestion(input);
    }
  };

  const handleRetry = () => {
    if (lastSubmittedRef.current) {
      submitQuestion(lastSubmittedRef.current);
    }
  };

  const charCount = input.length;
  const isOverLimit = charCount > MAX_CHARS;

  const formatResetTime = (isoString?: string) => {
    if (!isoString) return 'midnight UTC';
    try {
      const d = new Date(isoString);
      return `${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${d.toLocaleDateString()})`;
    } catch {
      return isoString;
    }
  };

  const getRouteLabel = (route?: string | null) => {
    if (!route) return 'General';
    const r = route.toLowerCase();
    if (r.includes('applicab')) return 'Applicability';
    if (r.includes('field')) return 'Field Check';
    return 'General';
  };

  // Render answer text, detecting "confirm with LHDN" line and styling it as quiet callout
  const renderFormattedAnswer = (text: string) => {
    const lines = text.split('\n');
    const elements: React.ReactNode[] = [];
    let buffer: string[] = [];

    lines.forEach((line, idx) => {
      const isLhdnNotice = /confirm.*with LHDN/i.test(line);

      if (isLhdnNotice) {
        if (buffer.length > 0) {
          elements.push(
            <div key={`p-${idx}`} style={{ whiteSpace: 'pre-wrap', marginBottom: '8px' }}>
              {buffer.join('\n')}
            </div>
          );
          buffer = [];
        }
        elements.push(
          <div key={`notice-${idx}`} className="lhdn-notice-box">
            {line.replace(/^["'\s]+|["'\s]+$/g, '')}
          </div>
        );
      } else {
        buffer.push(line);
      }
    });

    if (buffer.length > 0) {
      elements.push(
        <div key="p-last" style={{ whiteSpace: 'pre-wrap' }}>
          {buffer.join('\n')}
        </div>
      );
    }

    return elements;
  };

  return (
    <div className="view-container">
      <div className="chat-thread">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h3>Ask anything about e-Invoice rules</h3>
            <p>
              Answers cite the official IRBM Guideline and FAQ versions and sections.
            </p>
            <div className="sample-prompts">
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('What is the exemption threshold for e-Invoice implementation?')}
              >
                &ldquo;What is the exemption threshold for e-Invoice implementation?&rdquo; &rarr;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('My business started in 2024 with RM2M turnover. When must I implement e-Invoice?')}
              >
                &ldquo;My business started in 2024 with RM2M turnover. When must I implement e-Invoice?&rdquo; &rarr;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('Can I issue a consolidated e-Invoice for a RM12,000 sale?')}
              >
                &ldquo;Can I issue a consolidated e-Invoice for a RM12,000 sale?&rdquo; &rarr;
              </button>
            </div>
          </div>
        ) : (
          messages.map((m) => {
            if (m.role === 'user') {
              return (
                <div key={m.id} className="msg-row user">
                  <div className="msg-bubble-user">{m.content}</div>
                </div>
              );
            }

            return (
              <div key={m.id} className="msg-row assistant">
                <div className="assistant-area">
                  <div className="msg-header-bar">
                    <span className="route-badge">
                      {getRouteLabel(m.route)}
                    </span>
                  </div>

                  <div className="answer-body">
                    {renderFormattedAnswer(m.content)}
                  </div>

                  {m.citations && m.citations.length > 0 && (
                    <div className="citations-block">
                      <ul className="citations-list">
                        {m.citations.map((c: Citation, cIdx: number) => (
                          <li key={cIdx} className="citation-item">
                            &sect; <strong>{c.doc}</strong> v{c.version} &sect;{c.section}, p{c.page}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {loading && (
          <div className="msg-row assistant">
            <div className="loading-text">Checking the guidelines…</div>
          </div>
        )}

        {quotaExhaustedError && (
          <div className="quota-box" role="alert">
            <p>
              Daily question quota is used up. Resets at {formatResetTime(quotaExhaustedError.resets_at)}.
            </p>
            <button
              type="button"
              className="quota-switch-link"
              onClick={onSwitchToCheckInvoice}
            >
              Switch to Check Invoice (no AI quota needed) &rarr;
            </button>
          </div>
        )}

        {rateLimitError && (
          <div className="error-callout" role="alert">
            <span>{rateLimitError}</span>
          </div>
        )}

        {networkError && (
          <div className="error-callout" role="alert">
            <span>{networkError}</span>
            <button type="button" className="retry-btn" onClick={handleRetry}>
              Retry
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="chat-form-container">
        <form onSubmit={handleSubmit}>
          <div className="chat-input-box">
            <textarea
              ref={textareaRef}
              className="chat-textarea"
              placeholder={isHealthy ? "Ask a question… (Enter to send, Shift+Enter for newline)" : "Waking up…"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading || !isHealthy}
              rows={2}
            />

            <div className="chat-form-bottom">
              <span className={`char-counter ${isOverLimit ? 'limit-exceeded' : ''}`}>
                {charCount.toLocaleString()} / {MAX_CHARS.toLocaleString()}
              </span>

              <div className="chat-actions">
                {messages.length > 0 && (
                  <button
                    type="button"
                    className="btn-reset"
                    onClick={handleResetSession}
                    disabled={loading}
                  >
                    Clear
                  </button>
                )}
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !isHealthy || !input.trim() || isOverLimit}
                >
                  Ask
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
