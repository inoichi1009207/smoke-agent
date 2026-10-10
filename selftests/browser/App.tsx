import { Link, Route, Routes, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import apiClient from './api';
import { load, loadNow, peek, postNow, preload, put, refresh, reset, Superseded, useData } from './api/store';

preload(['/me', '/items', '/private']);

type Item = { id: number; name: string };

function List({ items }: { items: Item[] }) {
  return <ul>{items.map((i) => <li key={i.id}>{i.name}</li>)}</ul>;
}

function StorePage() {
  return <List items={useData<Item[]>('/items') ?? []} />;
}

function FetchPage() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    apiClient.get('/items').then((r) => setItems(r.data));
  }, []);
  return <List items={items} />;
}

function PrivatePage() {
  const items = useData<Item[] | null>('/private');
  if (items === undefined) return <p>Not loaded</p>;
  if (items === null) return <p>Null cached</p>;
  return <List items={items} />;
}

function NewItem() {
  const navigate = useNavigate();
  const [error, setError] = useState('');
  async function save() {
    await apiClient.post('/items', { name: 'Shelf New' });
    await refresh(['/items']);
    navigate('/store');
  }
  async function saveWhileLoading() {
    void load('/items').catch(() => undefined);
    await new Promise((resolve) => setTimeout(resolve, 50));
    await apiClient.post('/items', { name: 'Shelf Racing' });
    await refresh(['/items']);
    navigate('/store');
  }
  async function saveBroken() {
    try {
      await refresh(['/broken']);
      navigate('/store');
    } catch {
      setError('Save failed');
    }
  }
  return (
    <>
      <button type="button" onClick={save}>Save item</button>
      <button type="button" onClick={saveWhileLoading}>Save while loading</button>
      <button type="button" onClick={saveBroken}>Save broken</button>
      {error ? <p role="alert">{error}</p> : null}
    </>
  );
}

function DetailsPage() {
  return <List items={useData<Item[]>('/items?view=details') ?? []} />;
}

function Lab() {
  const navigate = useNavigate();
  const [result, setResult] = useState('');
  function putPartialThenOpen() {
    put('/items?view=details', [{ id: 0, name: 'Partial answer' }]);
    navigate('/details');
  }
  function putEditThenOpen() {
    put('/items', [{ id: 1, name: 'Shelf Optimistic' }]);
    navigate('/store');
  }
  async function resetThenLoad() {
    void load('/items').catch(() => undefined);
    reset();
    const value = await load<Item[]>('/items');
    setResult(value ? 'reloaded: yes' : 'reloaded: no');
  }
  async function refreshAcrossReset() {
    void load('/items').catch(() => undefined);
    const queued = refresh(['/items']);
    reset();
    try {
      await queued;
      setResult('refresh: succeeded');
    } catch (error) {
      setResult(error instanceof Superseded ? 'refresh: superseded' : 'refresh: other error');
    }
  }
  async function refreshSignedOut() {
    try {
      await refresh(['/private']);
      setResult('private refresh: succeeded');
    } catch {
      setResult('private refresh: rejected');
    }
  }
  async function editWhileLoading() {
    void load('/items', true).catch(() => undefined);
    put('/items', [{ id: 1, name: 'Shelf Edited' }]);
    await new Promise((resolve) => setTimeout(resolve, 500));
    setResult('after edit: ' + (peek<Item[]>('/items') ?? []).map((i) => i.name).join(','));
  }
  return (
    <>
      <button type="button" onClick={editWhileLoading}>Edit while loading</button>
      <button type="button" onClick={putPartialThenOpen}>Put partial then open</button>
      <button type="button" onClick={putEditThenOpen}>Put edit then reopen</button>
      <button type="button" onClick={resetThenLoad}>Reset then load</button>
      <button type="button" onClick={refreshAcrossReset}>Refresh across reset</button>
      <button type="button" onClick={refreshSignedOut}>Refresh private signed out</button>
      <p>{result}</p>
    </>
  );
}

function Nav() {
  const me = useData<{ nickname: string } | null>('/me');
  const navigate = useNavigate();
  async function login() {
    const r = await apiClient.post('/login');
    await refresh(['/private']);
    put('/me', r.data);
    navigate('/');
  }
  function loginNow() {
    const user = postNow<{ nickname: string }>('/login', {});
    loadNow(['/private']);
    put('/me', user);
    navigate('/');
  }
  async function logout() {
    await apiClient.post('/logout');
    reset();
    put('/me', null);
    navigate('/');
  }
  async function logoutWhileLoading() {
    await apiClient.post('/slow-private');
    void load('/private', true).catch(() => undefined);
    await logout();
  }
  return (
    <nav>
      <Link to="/">Home</Link> <Link to="/store">Store shelves</Link> <Link to="/fetch">Fetch shelves</Link>{' '}
      <Link to="/private">Private</Link> <Link to="/new">New</Link> <Link to="/lab">Lab</Link>{' '}
      {me ? (
        <>
          <span>{me.nickname}</span> <button type="button" onClick={logout}>Logout</button>{' '}
          <button type="button" onClick={logoutWhileLoading}>Logout while loading</button>
        </>
      ) : (
        <>
          <button type="button" onClick={login}>Login</button>{' '}
          <button type="button" onClick={loginNow}>Login now</button>
        </>
      )}
    </nav>
  );
}

function ListShapes() {
  const [opened, setOpened] = useState('');
  return (
    <div>
      <ul>
        <li><h3><button type="button" onClick={() => setOpened('Book 5')}>Book 5</button></h3></li>
        <li><h3>Book 6</h3><button type="button" onClick={() => setOpened('Book 6')}>Book 6</button></li>
      </ul>
      <p>opened: {opened}</p>
    </div>
  );
}

function App() {
  return (
    <>
      <Nav />
      <Routes>
        <Route path="/" element={<h1>Home</h1>} />
        <Route path="/list" element={<ListShapes />} />
        <Route path="/store" element={<StorePage />} />
        <Route path="/fetch" element={<FetchPage />} />
        <Route path="/private" element={<PrivatePage />} />
        <Route path="/new" element={<NewItem />} />
        <Route path="/lab" element={<Lab />} />
        <Route path="/details" element={<DetailsPage />} />
      </Routes>
    </>
  );
}

export default App;
