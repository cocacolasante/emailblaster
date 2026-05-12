import { Component } from 'react';

/**
 * Class-based error boundary. Catches render errors in the subtree and
 * shows a recoverable fallback UI instead of a blank page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info?.componentStack);
  }

  handleReset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div data-testid="error-boundary" style={containerStyle}>
          <h2 style={{ margin: 0 }}>Something went wrong</h2>
          <p style={{ color: '#666' }}>
            {this.state.error?.message || 'An unexpected error occurred.'}
          </p>
          <button type="button" onClick={this.handleReset} style={btnStyle}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const containerStyle = {
  padding: 32,
  margin: 24,
  border: '1px solid #fde68a',
  background: '#fffbeb',
  borderRadius: 8,
  color: '#7a5a00',
};

const btnStyle = {
  marginTop: 12,
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};
