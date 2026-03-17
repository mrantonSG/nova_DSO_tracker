/**
 * Blog JavaScript - Nova DSO Tracker
 * Handles image previews, AJAX image deletion, and form validation
 */

// Track selected files for upload
let selectedFiles = new DataTransfer();

/**
 * Initialize blog form functionality
 * @param {Object} config - Configuration object
 * @param {boolean} config.isEdit - Whether this is an edit form
 * @param {number|null} config.postId - Post ID for edit mode
 * @param {string} config.csrfToken - CSRF token for AJAX requests
 */
function initBlogForm(config) {
    const fileInput = document.getElementById('images');
    const uploadZone = document.getElementById('upload-zone');
    const previewContainer = document.getElementById('image-previews');
    
    if (!fileInput || !previewContainer) return;
    
    // File input change handler
    fileInput.addEventListener('change', function(e) {
        handleFiles(e.target.files, previewContainer, config);
    });
    
    // Drag and drop
    if (uploadZone) {
        uploadZone.addEventListener('dragover', function(e) {
            e.preventDefault();
            uploadZone.classList.add('drag-over');
        });
        
        uploadZone.addEventListener('dragleave', function(e) {
            e.preventDefault();
            uploadZone.classList.remove('drag-over');
        });
        
        uploadZone.addEventListener('drop', function(e) {
            e.preventDefault();
            uploadZone.classList.remove('drag-over');
            
            const files = e.dataTransfer.files;
            handleFiles(files, previewContainer, config);
            
            // Update file input
            for (let i = 0; i < files.length; i++) {
                selectedFiles.items.add(files[i]);
            }
            fileInput.files = selectedFiles.files;
        });
    }
    
    // Existing image delete buttons (edit mode)
    if (config.isEdit) {
        initExistingImageDelete(config);
    }
}

/**
 * Handle selected files and create previews
 */
function handleFiles(files, container, config) {
    const fileInput = document.getElementById('images');
    
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        
        // Validate file type
        if (!file.type.match(/^image\/(png|jpeg|gif)$/)) {
            console.warn('Skipping non-image file:', file.name);
            continue;
        }
        
        // Validate file size (10MB max)
        if (file.size > 10 * 1024 * 1024) {
            alert(`File "${file.name}" is too large. Maximum size is 10MB.`);
            continue;
        }
        
        // Add to DataTransfer for form submission
        selectedFiles.items.add(file);
        
        // Create preview
        createImagePreview(file, container, selectedFiles.items.length - 1, config);
    }
    
    // Update file input
    fileInput.files = selectedFiles.files;
}

/**
 * Create image preview element
 */
function createImagePreview(file, container, index, config) {
    const reader = new FileReader();
    
    reader.onload = function(e) {
        const previewItem = document.createElement('div');
        previewItem.className = 'blog-image-preview-item';
        previewItem.dataset.index = index;
        
        previewItem.innerHTML = `
            <img src="${e.target.result}" alt="Preview">
            <button type="button" class="blog-image-preview-remove" data-index="${index}">&times;</button>
            <div class="blog-image-preview-caption">
                <input type="text" name="captions" placeholder="Caption (optional)">
            </div>
        `;
        
        container.appendChild(previewItem);
        
        // Remove button handler
        const removeBtn = previewItem.querySelector('.blog-image-preview-remove');
        removeBtn.addEventListener('click', function() {
            removePreviewImage(index, previewItem, config);
        });
    };
    
    reader.readAsDataURL(file);
}

/**
 * Remove a preview image
 */
function removePreviewImage(index, element, config) {
    const fileInput = document.getElementById('images');
    
    // Remove from DataTransfer
    const newDataTransfer = new DataTransfer();
    const files = selectedFiles.files;
    
    for (let i = 0; i < files.length; i++) {
        if (i !== index) {
            newDataTransfer.items.add(files[i]);
        }
    }
    
    selectedFiles = newDataTransfer;
    fileInput.files = selectedFiles.files;
    
    // Remove preview element
    element.remove();
    
    // Update remaining indices
    const previewContainer = document.getElementById('image-previews');
    const items = previewContainer.querySelectorAll('.blog-image-preview-item');
    items.forEach((item, i) => {
        item.dataset.index = i;
        item.querySelector('.blog-image-preview-remove').dataset.index = i;
    });
}

/**
 * Initialize existing image delete functionality (edit mode)
 */
function initExistingImageDelete(config) {
    const existingImagesContainer = document.getElementById('existing-images');
    if (!existingImagesContainer) return;
    
    existingImagesContainer.addEventListener('click', function(e) {
        const removeBtn = e.target.closest('.blog-remove-existing-image');
        if (!removeBtn) return;
        
        e.preventDefault();
        
        const imageId = removeBtn.dataset.imageId;
        const postId = removeBtn.dataset.postId;
        const imageItem = removeBtn.closest('.blog-existing-image-item');
        
        if (!confirm('Remove this image from the post?')) return;
        
        // AJAX delete
        fetch(`/blog/${postId}/delete-image/${imageId}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': config.csrfToken,
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                imageItem.remove();
            } else {
                alert('Failed to remove image: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error removing image:', error);
            alert('Failed to remove image. Please try again.');
        });
    });
}

/**
 * Comment character counter
 * (Also initialized in blog_detail.html inline script, but keeping here for completeness)
 */
function initCommentCounter() {
    const commentArea = document.getElementById('comment_content');
    const charCount = document.getElementById('char-count');
    
    if (commentArea && charCount) {
        commentArea.addEventListener('input', function() {
            charCount.textContent = this.value.length;
        });
    }
}

// Auto-initialize on DOM ready
document.addEventListener('DOMContentLoaded', function() {
    // Comment counter (for detail page)
    initCommentCounter();
});
