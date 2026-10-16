import { mount } from 'svelte'
import './app.css'
import Chat from './Chat.svelte'

mount(Chat, { target: document.getElementById('app')! })
