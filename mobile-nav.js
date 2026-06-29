/**
 * mobile-nav.js — Mobile hamburger menu toggle
 * Handles sidebar show/hide on mobile screens
 */

document.addEventListener('DOMContentLoaded', function() {
  const sidebar = document.querySelector('.sidebar');
  const main = document.querySelector('.main');
  let toggleBtn = document.querySelector('.mobile-nav-toggle');

  // Create toggle button if not exists
  if (!toggleBtn && window.innerWidth <= 900) {
    toggleBtn = document.createElement('button');
    toggleBtn.className = 'mobile-nav-toggle';
    toggleBtn.innerHTML = '☰';
    toggleBtn.type = 'button';
    document.body.appendChild(toggleBtn);
  }

  // Store state
  let sidebarOpen = false;

  // Toggle function
  function toggleSidebar() {
    sidebarOpen = !sidebarOpen;
    if (sidebar) {
      sidebar.style.display = sidebarOpen ? 'flex' : 'none';
    }
    if (toggleBtn) {
      toggleBtn.innerHTML = sidebarOpen ? '✕' : '☰';
      toggleBtn.style.zIndex = sidebarOpen ? '999' : '1000';
    }
  }

  // Close sidebar when clicking on nav items
  if (sidebar) {
    sidebar.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', () => {
        if (window.innerWidth <= 900) {
          sidebarOpen = false;
          sidebar.style.display = 'none';
          if (toggleBtn) toggleBtn.innerHTML = '☰';
        }
      });
    });
  }

  // Close sidebar when clicking outside
  document.addEventListener('click', (e) => {
    if (sidebarOpen && sidebar && toggleBtn) {
      if (!sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
        sidebarOpen = false;
        sidebar.style.display = 'none';
        if (toggleBtn) toggleBtn.innerHTML = '☰';
      }
    }
  });

  // Attach event listener to toggle button
  if (toggleBtn) {
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSidebar();
    });
  }

  // Handle window resize
  window.addEventListener('resize', () => {
    if (window.innerWidth > 900) {
      sidebarOpen = false;
      if (sidebar) sidebar.style.display = 'flex';
      if (toggleBtn) toggleBtn.style.display = 'none';
    } else {
      if (toggleBtn) toggleBtn.style.display = 'flex';
      if (sidebar && !sidebarOpen) sidebar.style.display = 'none';
    }
  });

  // Initial state on mobile
  if (window.innerWidth <= 900) {
    if (sidebar) sidebar.style.display = 'none';
    if (toggleBtn) toggleBtn.style.display = 'flex';
  }
});
