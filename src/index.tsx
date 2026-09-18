// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React from 'react';
import './index.css';

import './i18n';

import store, { persistor } from './app/store'
import { Provider } from 'react-redux'

import { AppFC } from './app/App';

import { PersistGate } from 'redux-persist/integration/react'
import { createRoot } from 'react-dom/client';
import { MultiuserWorkspace } from './views/MultiuserWorkspace';


const domNode = document.getElementById('root') as HTMLElement;
const root = createRoot(domNode);


root.render(window.location.pathname === '/multiuser' ? <MultiuserWorkspace /> : <React.StrictMode>
        <Provider store={store}>
            <PersistGate loading={null} persistor={persistor}>
                <AppFC />
            </PersistGate>
        </Provider>
</React.StrictMode>);
